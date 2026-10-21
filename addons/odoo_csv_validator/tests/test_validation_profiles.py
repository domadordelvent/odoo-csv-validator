import base64

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tests import Form
from odoo.tools import mute_logger
from psycopg2 import IntegrityError


@tagged('post_install', '-at_install')
class TestValidationProfiles(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = 'odoo_csv_validator.group_validation_profiles'
        cls.alice = new_test_user(cls.env, login='profile_alice', groups=group)
        cls.bob = new_test_user(cls.env, login='profile_bob', groups=group)
        cls.ordinary = new_test_user(cls.env, login='profile_internal', groups='base.group_user')
        cls.portal = new_test_user(cls.env, login='profile_portal', groups='base.group_portal')

    def profile(self, **rule):
        return self.env['csv.validation.profile'].with_user(self.alice).create({
            'name': 'Arbitrary CSV', 'rule_ids': [Command.create(dict(column_name='n', **rule))],
        })

    def wizard(self, profile=False, data=b'n\n1\n1\n'):
        return self.env['csv.validation.wizard'].with_user(self.alice).create({
            'filename': 'values.csv', 'file_data': base64.b64encode(data),
            'profile_id': profile.id if profile else False,
        })

    def test_profile_crud(self):
        profile = self.profile(required=True)
        self.assertEqual(profile.owner_id, self.alice)
        profile.write({'name': 'Renamed'})
        profile.rule_ids.write({'unique': True})
        self.assertEqual(profile.name, 'Renamed')
        profile.unlink()
        self.assertFalse(profile.exists())

    def test_combined_rules_export_and_snapshot(self):
        profile = self.profile(required=True, unique=True, data_type='integer', use_minimum=True, minimum='0')
        wizard = self.wizard(profile, b'n\n""\nx\n-1\n2\n2')
        original = wizard.file_data
        wizard.action_validate()
        self.assertEqual(wizard.issue_ids.mapped('code'), ['REQUIRED', 'INVALID_NUMBER', 'BELOW_MINIMUM', 'DUPLICATE_VALUE'])
        self.assertEqual(wizard.issue_ids[-1].first_line, 5)
        self.assertIn(b'DUPLICATE_VALUE', base64.b64decode(wizard.report_data))
        self.assertEqual(wizard.file_data, original)
        self.assertEqual(wizard.profile_snapshot['rules'][0]['kind'], 'integer')
        profile.rule_ids.write({'minimum': '10'})
        self.assertEqual(wizard.profile_snapshot['rules'][0]['minimum'], '0')
        wizard.action_validate()
        self.assertEqual(wizard.profile_snapshot['rules'][0]['minimum'], '10')

    def test_decimal_comma(self):
        profile = self.profile(data_type='decimal', use_minimum=True, minimum='1.5')
        profile.decimal_separator = ','
        wizard = self.wizard(profile, b'n;label\n1,5;ok\n1,4;bad')
        wizard.delimiter = ';'
        wizard.action_validate()
        self.assertEqual(wizard.issue_ids.mapped('code'), ['BELOW_MINIMUM'])

    def test_missing_and_duplicate_headers(self):
        profile = self.profile(required=True)
        for data, code in ((b'other\n1', 'RULE_COLUMN_MISSING'), (b'n,n\n1,2', 'RULE_CONFIGURATION')):
            wizard = self.wizard(profile, data)
            wizard.action_validate()
            self.assertEqual(wizard.state, 'incomplete')
            self.assertIn(code, wizard.issue_ids.mapped('code'))

    def test_no_profile_is_structural_only(self):
        wizard = self.wizard(data=b'n\nx\nx\n')
        wizard.action_validate()
        self.assertEqual(wizard.state, 'valid')
        self.assertFalse(wizard.profile_snapshot)

    def test_profile_change_invalidates_result(self):
        wizard = self.wizard()
        wizard.action_validate()
        wizard.profile_id = self.profile(unique=True)
        self.assertEqual(wizard.state, 'draft')
        self.assertFalse(wizard.report_data)
        wizard.action_validate()
        wizard.profile_id = False
        self.assertEqual(wizard.state, 'draft')

    def test_profile_delete_keeps_result_snapshot(self):
        profile = self.profile(unique=True)
        wizard = self.wizard(profile)
        wizard.action_validate()
        profile.unlink()
        wizard.invalidate_recordset()
        self.assertFalse(wizard.profile_id)
        self.assertTrue(wizard.report_data)
        self.assertEqual(wizard.applied_profile_name, 'Arbitrary CSV')

    def test_ordinary_and_portal_users_denied(self):
        for user in (self.ordinary, self.portal):
            with self.assertRaises(AccessError), self.cr.savepoint():
                self.env['csv.validation.profile'].with_user(user).create({'name': 'Forbidden'})

    def test_private_profiles_and_rules(self):
        profile = self.profile(unique=True)
        for record in (profile, profile.rule_ids):
            other = record.with_user(self.bob)
            with self.assertRaises(AccessError):
                other.read()
            with self.assertRaises(AccessError):
                other.write({'name': 'stolen'} if record._name == 'csv.validation.profile' else {'unique': False})
            with self.assertRaises(AccessError):
                other.unlink()
        self.assertFalse(profile.with_user(self.bob).search([('id', '=', profile.id)]))

    def test_cannot_link_foreign_profile_or_rule(self):
        profile = self.profile()
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.env['csv.validation.profile.rule'].with_user(self.bob).create({'profile_id': profile.id, 'column_name': 'x'})
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.env['csv.validation.wizard'].with_user(self.bob).create({
                'file_data': base64.b64encode(b'n\n1'), 'profile_id': profile.id,
            })
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.env['csv.validation.wizard'].with_user(self.ordinary).create({
                'file_data': base64.b64encode(b'n\n1'), 'profile_id': profile.id,
            })

    def test_ownership_cannot_be_forged(self):
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.env['csv.validation.profile'].with_user(self.alice).create({'name': 'Forged', 'owner_id': self.bob.id})
        profile = self.profile()
        with self.assertRaises(AccessError):
            profile.owner_id = self.bob

    def test_invalid_configuration_rejected(self):
        for options in ({'column_name': ' '}, {'use_minimum': True, 'minimum': '0'},
                        {'data_type': 'integer', 'use_minimum': True, 'minimum': 'NaN'},
                        {'data_type': 'decimal', 'use_minimum': True}):
            with self.subTest(options=options), self.assertRaises(ValidationError), self.cr.savepoint():
                self.env['csv.validation.profile'].with_user(self.alice).create({
                    'name': 'Invalid', 'rule_ids': [Command.create(dict({'column_name': 'n'}, **options))],
                })

    def test_rule_count_and_unique_count_limits(self):
        for count, unique in ((101, False), (21, True)):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                self.env['csv.validation.profile'].with_user(self.alice).create({
                    'name': 'Too many', 'rule_ids': [Command.create({'column_name': str(i), 'unique': unique}) for i in range(count)],
                })

    @mute_logger('odoo.sql_db')
    def test_duplicate_column_configuration_rejected(self):
        profile = self.profile()
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            profile.write({'rule_ids': [Command.create({'column_name': ' n ', 'required': True})]})

    def test_profile_and_wizard_forms(self):
        with Form(self.env['csv.validation.profile'].with_user(self.alice)) as form:
            form.name = 'Configured through standard forms'
            with form.rule_ids.new() as line:
                line.column_name = 'quantity'
                line.required = True
                line.unique = True
                line.data_type = 'integer'
                line.use_minimum = True
                line.minimum = '0'
        profile = form.record
        with Form(self.env['csv.validation.wizard'].with_user(self.alice).with_context(default_filename='arbitrary.csv')) as wizard_form:
            wizard_form.file_data = base64.b64encode(b'quantity\n-1')
            wizard_form.profile_id = profile
        wizard = wizard_form.record
        wizard.action_validate()
        self.assertEqual(wizard.issue_ids.code, 'BELOW_MINIMUM')

    def test_revoked_profile_permission_never_downgrades_to_structure(self):
        profile = self.profile(required=True)
        wizard = self.wizard(profile)
        group = self.env.ref('odoo_csv_validator.group_validation_profiles')
        self.alice.write({'groups_id': [Command.unlink(group.id)]})
        with self.assertRaises(AccessError):
            wizard.action_validate()
