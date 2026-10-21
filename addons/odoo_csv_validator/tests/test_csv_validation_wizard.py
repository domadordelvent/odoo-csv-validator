import base64
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestCsvValidationWizard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(cls.env, login='csv_alice', groups='base.group_user')
        cls.bob = new_test_user(cls.env, login='csv_bob', groups='base.group_user')
        cls.portal = new_test_user(cls.env, login='csv_portal', groups='base.group_portal')

    def wizard(self, data=b'a,b\n1\n'):
        return self.env['csv.validation.wizard'].with_user(self.alice).create({
            'file_data': base64.b64encode(data), 'filename': 'input.csv',
        })

    def test_validation_and_export_preserve_input(self):
        wizard = self.wizard()
        original = wizard.file_data
        wizard.action_validate()
        self.assertEqual(wizard.state, 'errors')
        self.assertEqual(wizard.record_count, 1)
        self.assertEqual(wizard.issue_ids.line, 2)
        self.assertIn(b'COLUMN_COUNT', base64.b64decode(wizard.report_data))
        self.assertEqual(wizard.file_data, original)

    def test_settings_change_invalidates_results(self):
        wizard = self.wizard()
        wizard.action_validate()
        wizard.write({'delimiter': ';'})
        self.assertEqual(wizard.state, 'draft')
        self.assertFalse(wizard.report_data)
        self.assertFalse(wizard.issue_ids)

    def test_cross_user_access(self):
        wizard = self.wizard()
        wizard.action_validate()
        with self.assertRaises(AccessError):
            wizard.with_user(self.bob).read(['file_data', 'report_data'])
        with self.assertRaises(AccessError):
            wizard.with_user(self.bob).action_validate()
        with self.assertRaises(AccessError):
            wizard.issue_ids.with_user(self.bob).read(['message'])
        with self.assertRaises(AccessError):
            wizard.issue_ids.with_user(self.bob).read([
                'column_name', 'column_label', 'user_message', 'restriction_details',
            ])

    def test_portal_has_no_access(self):
        with self.assertRaises(AccessError):
            self.env['csv.validation.wizard'].with_user(self.portal).create({'file_data': base64.b64encode(b'a')})

    def test_upload_limit(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.wizard(b'a' * (10 * 1024 * 1024 + 1))

    @mute_logger('odoo.addons.odoo_csv_validator.wizard.csv_validation_wizard')
    def test_unexpected_error_is_controlled(self):
        wizard = self.wizard()
        module = 'odoo.addons.odoo_csv_validator.wizard.csv_validation_wizard.validate_csv'
        with patch(module, side_effect=RuntimeError('private-content')):
            with self.assertRaises(UserError) as error:
                wizard.action_validate()
        self.assertNotIn('private-content', str(error.exception))
        self.assertFalse(wizard.report_data)

    def test_cannot_link_issue_to_another_users_wizard(self):
        wizard = self.wizard()
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.env['csv.validation.issue'].with_user(self.bob).create({
                'wizard_id': wizard.id, 'code': 'FORGED',
            })

    def test_success_and_encoding_failure(self):
        valid = self.wizard(b'a,b\n1,2')
        valid.action_validate()
        self.assertEqual(valid.state, 'valid')
        invalid = self.wizard(b'a\n\xff')
        invalid.action_validate()
        self.assertEqual(invalid.state, 'incomplete')

    def test_readable_names_follow_filename_and_validation(self):
        wizard = self.wizard()
        self.assertEqual(wizard.display_name, 'CSV validation: input.csv')
        action = wizard.action_validate()
        self.assertEqual(action['name'], 'CSV Validator')
        self.assertEqual(wizard.display_name, 'CSV validation: input.csv')
        wizard.filename = 'another.csv'
        self.assertEqual(wizard.display_name, 'CSV validation: another.csv')
        wizard.filename = False
        self.assertEqual(wizard.display_name, 'CSV validation')


    def test_result_fields_reject_create_and_write(self):
        wizard = self.wizard()
        wizard.action_validate()
        forged = {
            'state': 'valid', 'record_count': 999, 'blank_line_count': 999,
            'issue_count': 0, 'details_truncated': False,
            'report_data': base64.b64encode(b'forged'), 'report_filename': 'forged.csv',
            'applied_profile_name': 'forged', 'profile_snapshot': {'name': 'forged'},
            'header_snapshot': ['forged'], 'issue_ids': [(5, 0, 0)],
            'presentation_headers': ['forged'], 'create_uid': self.bob.id,
        }
        for field, value in forged.items():
            with self.subTest(field=field):
                with self.assertRaises(AccessError), self.cr.savepoint():
                    wizard.write({field: value})
                with self.assertRaises(AccessError), self.cr.savepoint():
                    wizard.create({'file_data': base64.b64encode(b'a'), field: value})
        self.assertEqual(wizard.state, 'errors')
        self.assertEqual(wizard.issue_count, 1)

    def test_context_cannot_inject_results_or_unlock_writes(self):
        model = self.env['csv.validation.wizard'].with_user(self.alice).with_context(
            default_state='valid', default_issue_count=99,
            default_header_snapshot=['forged'], default_report_data=base64.b64encode(b'forged'),
            default_issue_ids=[(0, 0, {'code': 'FORGED'})],
            internal_validation=True, validation_process=True, sudo=True)
        wizard = model.create({'file_data': base64.b64encode(b'a,b\n1')})
        self.assertEqual(wizard.state, 'draft')
        self.assertEqual(wizard.issue_count, 0)
        self.assertFalse(wizard.report_data)
        self.assertFalse(wizard.header_snapshot)
        self.assertFalse(wizard.issue_ids)
        with self.assertRaises(AccessError):
            wizard.write({'state': 'valid', 'delimiter': ';'})
        wizard.action_validate()
        self.assertEqual(wizard.state, 'errors')
        self.assertEqual(wizard.issue_ids.code, 'COLUMN_COUNT')

    def test_issue_mutations_are_rejected(self):
        wizard = self.wizard()
        wizard.action_validate()
        other = self.wizard()
        with self.assertRaises(AccessError):
            wizard.issue_ids.create({'wizard_id': wizard.id, 'code': 'FORGED'})
        for vals in ({'message': 'forged'}, {'wizard_id': other.id}, {'column_label': 'forged'}):
            with self.assertRaises(AccessError):
                wizard.issue_ids.with_context(internal_validation=True).write(vals)
        with self.assertRaises(AccessError):
            wizard.issue_ids.unlink()
        self.assertEqual(wizard.issue_ids.code, 'COLUMN_COUNT')

    def test_revalidation_invalidation_and_parent_deletion(self):
        wizard = self.wizard()
        wizard.action_validate()
        original = wizard.issue_ids
        wizard.action_validate()
        self.assertEqual(len(wizard.issue_ids), 1)
        self.assertFalse(original.exists())
        wizard.write({'file_data': base64.b64encode(b'a,b\n1,2')})
        self.assertEqual(wizard.state, 'draft')
        self.assertFalse(wizard.issue_ids)
        wizard.action_validate()
        self.assertEqual(wizard.state, 'valid')
        wizard.unlink()
        self.assertFalse(wizard.exists())

    def test_superuser_maintenance_preserves_owner(self):
        wizard = self.wizard()
        wizard.action_validate()
        wizard.sudo().write({'header_snapshot': False})
        wizard.issue_ids.sudo().write({'message': 'Maintenance'})
        wizard.sudo().action_validate()
        self.assertEqual(wizard.create_uid, self.alice)
        self.assertEqual(wizard.issue_ids.code, 'COLUMN_COUNT')
        with self.assertRaises(AccessError):
            wizard.with_user(self.bob).action_validate()

    def test_admin_group_does_not_unlock_result_writes(self):
        admin = new_test_user(self.env, login='csv_settings_admin', groups='base.group_system')
        wizard = self.env['csv.validation.wizard'].with_user(admin).create({
            'file_data': base64.b64encode(b'a')})
        with self.assertRaises(AccessError):
            wizard.write({'state': 'valid'})
        wizard.action_validate()
        self.assertEqual(wizard.state, 'valid')

    def test_report_storage_limit_including_bin_size_context(self):
        wizard = self.wizard().sudo().with_context(bin_size=True)
        module = 'odoo.addons.odoo_csv_validator.wizard.csv_validation_wizard'
        with patch(module + '.MAX_REPORT_BYTES', 32):
            wizard.write({'report_data': base64.b64encode(b'x' * 32)})
            with self.assertRaises(ValidationError), self.cr.savepoint():
                wizard.write({'report_data': base64.b64encode(b'x' * 33)})
            with self.assertRaises(ValidationError), self.cr.savepoint():
                wizard.write({'report_data': b'!!!!'})

    def test_snapshot_name_and_counter_storage_limits(self):
        wizard = self.wizard().sudo()
        for vals in ({'profile_snapshot': {'value': 'x' * (512 * 1024)}},
                     {'header_snapshot': ['x' * (512 * 1024)]},
                     {'applied_profile_name': 'é' * 2049},
                     {'report_filename': 'x' * 256}, {'record_count': -1}):
            with self.subTest(fields=list(vals)), self.assertRaises(ValidationError), self.cr.savepoint():
                wizard.write(vals)

    def test_issue_storage_limits_apply_to_privileged_maintenance(self):
        wizard = self.wizard()
        wizard.action_validate()
        issue = wizard.issue_ids.sudo()
        issue.write({'message': 'é' * 32768})
        for vals in ({'message': 'é' * 32769}, {'code': 'x' * 257},
                     {'column': 1001}, {'line': -1}):
            with self.subTest(fields=list(vals)), self.assertRaises(ValidationError), self.cr.savepoint():
                issue.write(vals)
        module = 'odoo.addons.odoo_csv_validator.wizard.csv_validation_wizard'
        with patch(module + '.MAX_ISSUES', 1):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                issue.create({'wizard_id': wizard.id, 'code': 'EXTRA'})

    def test_internal_oversized_result_rolls_back_previous_result(self):
        wizard = self.wizard()
        wizard.action_validate()
        previous_report = wizard.report_data
        previous_issues = wizard.issue_ids
        module = 'odoo.addons.odoo_csv_validator.wizard.csv_validation_wizard'
        with patch(module + '.MAX_ISSUE_MESSAGE_BYTES', 1):
            with self.assertRaises(ValidationError):
                wizard.action_validate()
        self.assertEqual(wizard.report_data, previous_report)
        self.assertEqual(wizard.issue_ids, previous_issues)
        self.assertEqual(wizard.state, 'errors')


    def test_nul_csv_never_reaches_text_or_json_columns(self):
        for data in (b'a\x00b\n1', b'a\n"multi\nvalue\x00"'):
            wizard = self.wizard(data)
            wizard.action_validate()
            self.assertEqual(wizard.state, 'incomplete')
            self.assertEqual(wizard.issue_ids.code, 'NUL_CHARACTER')
            self.assertFalse(wizard.header_snapshot)
            self.assertEqual(base64.b64decode(wizard.file_data), data)
            self.assertNotIn(b'\x00', base64.b64decode(wizard.report_data))
            self.cr.execute('SELECT header_snapshot, profile_snapshot FROM csv_validation_wizard WHERE id = %s', [wizard.id])
            self.assertFalse(any(self.cr.fetchone()))
            self.cr.execute('SELECT 1')
            self.assertEqual(self.cr.fetchone(), (1,))

    def test_nul_result_payload_rejected_before_sql(self):
        wizard = self.wizard()
        wizard.action_validate()
        previous_report = wizard.report_data
        for field, value in [('header_snapshot', ['a\x00b']),
                             ('profile_snapshot', {'rules': [{'column': 'a\x00b'}]}),
                             ('profile_snapshot', {'a\x00b': 'value'}),
                             ('applied_profile_name', 'a\x00b')]:
            with self.subTest(field=field), self.assertRaises(ValidationError):
                wizard._store_validation_result({field: value}, [])
            self.cr.execute('SELECT 1')
            self.assertEqual(self.cr.fetchone(), (1,))
        with self.assertRaises(ValidationError):
            wizard._store_validation_result({}, [{'code': 'CUSTOM', 'message': 'a\x00b'}])
        self.assertEqual(wizard.report_data, previous_report)
        self.assertEqual(wizard.issue_ids.code, 'COLUMN_COUNT')

    @mute_logger('odoo.addons.odoo_csv_validator.wizard.csv_validation_wizard', 'odoo.sql_db')
    def test_database_persistence_error_recovers_cursor_and_result(self):
        wizard = self.wizard()
        wizard.action_validate()
        previous_report, previous_issues = wizard.report_data, wizard.issue_ids
        unrelated = self.wizard(b'unrelated\n1')
        model = type(self.env['csv.validation.issue'])
        original = model._create_validation_issues

        def fail_after_insertion(records, target, values):
            original(records, target, values)
            records.env.cr.execute('SELECT 1 / 0')

        with patch.object(model, '_create_validation_issues', fail_after_insertion):
            with self.assertRaises(UserError) as error:
                wizard.action_validate()
        self.assertIn('could not be saved', str(error.exception))
        self.assertNotIn('division', str(error.exception).lower())
        self.cr.execute('SELECT 42')
        self.assertEqual(self.cr.fetchone(), (42,))
        self.assertEqual(wizard.report_data, previous_report)
        self.assertEqual(wizard.issue_ids, previous_issues)
        self.assertEqual(unrelated.filename, 'input.csv')
        unrelated.action_validate()
        self.assertEqual(unrelated.state, 'valid')

    @mute_logger('odoo.addons.odoo_csv_validator.wizard.csv_validation_wizard')
    def test_unexpected_persistence_error_is_generic_and_atomic(self):
        wizard = self.wizard()
        wizard.action_validate()
        previous = wizard.issue_ids
        model = type(self.env['csv.validation.issue'])
        with patch.object(model, '_create_validation_issues', side_effect=ValueError('private-content')):
            with self.assertRaises(UserError) as error:
                wizard.action_validate()
        self.assertNotIn('private-content', str(error.exception))
        self.assertEqual(wizard.issue_ids, previous)
        self.assertEqual(wizard.state, 'errors')

    def test_persistence_concurrency_errors_keep_odoo_retry_behavior(self):
        from psycopg2 import errors
        wizard = self.wizard()
        wizard.action_validate()
        model = type(self.env['csv.validation.issue'])
        for exception in (errors.SerializationFailure, errors.DeadlockDetected, errors.LockNotAvailable):
            with self.subTest(exception=exception), patch.object(model, '_create_validation_issues', side_effect=exception()):
                with self.assertRaises(exception):
                    wizard.action_validate()
            self.assertEqual(wizard.state, 'errors')
            self.cr.execute('SELECT 1')
            self.assertEqual(self.cr.fetchone(), (1,))
