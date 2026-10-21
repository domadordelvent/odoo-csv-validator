import base64
import csv
import io
from copy import deepcopy
from dataclasses import asdict

from odoo import Command
from odoo.tests import TransactionCase, new_test_user, tagged

from ..services.csv_validator import Issue, validate_csv
from ..services.column_rules import ColumnRule
from ..services.issue_presentation import describe_issue, export_presented_report, read_headers


@tagged('post_install', '-at_install')
class TestIssuePresentation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='presentation_user',
                                 groups='odoo_csv_validator.group_validation_profiles')

    def wizard(self, data, rules=(), **options):
        profile = False
        if rules:
            profile = self.env['csv.validation.profile'].with_user(self.user).create({
                'name': 'Presentation', 'rule_ids': [Command.create(rule) for rule in rules], **options,
            })
        wizard = self.env['csv.validation.wizard'].with_user(self.user).create({
            'file_data': base64.b64encode(data), 'filename': 'original.csv',
            'profile_id': profile.id if profile else False,
        })
        wizard.action_validate()
        return wizard

    def report_rows(self, wizard):
        return list(csv.DictReader(io.StringIO(base64.b64decode(wizard.report_data).decode('utf-8-sig'))))

    def test_named_column_and_simple_decimal_message(self):
        data = b'name,internal_reference\nAda,nope\n'
        wizard = self.wizard(data, [{'column_name': 'internal_reference', 'data_type': 'decimal'}])
        issue = wizard.issue_ids
        self.assertEqual(issue.column, 2)
        self.assertEqual(issue.column_name, 'internal_reference')
        self.assertEqual(issue.column_label, '2 — internal_reference')
        self.assertEqual(issue.issue_type, 'content')
        self.assertEqual(issue.user_message, 'Enter a valid decimal number (use "." as the decimal separator).')
        self.assertIn('1,024', issue.restriction_details)
        for restriction in ('0–9', '+ or -', 'scientific notation', 'thousands separators', 'NaN', 'infinity'):
            self.assertIn(restriction.lower(), issue.restriction_details.lower())
        self.assertEqual(base64.b64decode(wizard.file_data), data)
        row = self.report_rows(wizard)[0]
        self.assertEqual(row['column_name'], 'internal_reference')
        self.assertEqual(row['message'], issue.user_message)
        self.assertEqual(row['restrictions'], issue.restriction_details)
        self.assertEqual(row['technical_message'], issue.message)
        self.assertEqual(row['code'], 'INVALID_NUMBER')

    def test_structure_and_content_have_distinct_categories(self):
        wizard = self.wizard(b'n,other\nbad,x\n1\n', [{'column_name': 'n', 'data_type': 'integer'}])
        self.assertEqual(wizard.issue_ids.mapped('issue_type'), ['content', 'structure'])
        structural = wizard.issue_ids[1]
        self.assertFalse(structural.column_name)
        self.assertEqual(structural.column_label, 'Column not identified')
        self.assertEqual(structural.user_message, 'Wrong number of columns: 1 instead of 2.')
        self.assertEqual([row['issue_type'] for row in self.report_rows(wizard)[:-1]], ['content', 'structure'])

    def test_empty_and_duplicate_headers(self):
        wizard = self.wizard(b' id ,id, \n1,2,3')
        duplicate, empty = wizard.issue_ids
        self.assertEqual(duplicate.column_label, '2 — id')
        self.assertEqual(duplicate.user_message, 'This column name repeats column 1.')
        self.assertEqual(empty.column_label, '3 — Unnamed column')
        self.assertFalse(empty.column_name)

    def test_missing_column_does_not_invent_a_position(self):
        wizard = self.wizard(b'name\nAda', [{'column_name': 'missing', 'required': True}])
        issue = wizard.issue_ids
        self.assertFalse(issue.column_name)
        self.assertEqual(issue.column, 0)
        self.assertEqual(issue.user_message, 'The CSV has no column named "missing".')

    def test_encoding_error_keeps_location_and_original_bytes(self):
        data = b'n\n\xff'
        wizard = self.wizard(data)
        issue = wizard.issue_ids
        self.assertEqual(issue.issue_type, 'structure')
        self.assertFalse(issue.column_name)
        self.assertIn('byte offset 2', issue.restriction_details)
        self.assertEqual(issue.line, 2)
        self.assertEqual(base64.b64decode(wizard.file_data), data)

    def test_duplicate_metadata_minimum_and_integer_restrictions(self):
        wizard = self.wizard(b'n\nbad\n-1\n2\n2', [{
            'column_name': 'n', 'data_type': 'integer', 'unique': True,
            'use_minimum': True, 'minimum': '0',
        }])
        number, minimum, duplicate = wizard.issue_ids
        self.assertEqual(number.user_message, 'Enter a valid whole number.')
        self.assertIn('1,024', number.restriction_details)
        self.assertEqual(minimum.user_message, 'Enter a value of at least 0.')
        self.assertEqual(duplicate.user_message, 'This value is already used on line 4.')
        self.assertEqual(self.report_rows(wizard)[2]['first_occurrence_line'], '4')

    def test_decimal_separator_comes_from_run_snapshot(self):
        wizard = self.wizard(b'n\nbad', [{'column_name': 'n', 'data_type': 'decimal'}], decimal_separator=',')
        wizard.profile_id.decimal_separator = '.'
        self.assertIn('use ","', wizard.issue_ids.user_message)

    def test_header_reader_bom_multiline_and_syntax_failures(self):
        self.assertEqual(read_headers(b'\xef\xbb\xbf"multi\nline",b\n1,2', 'utf-8-sig', ','), ['multi\nline', 'b'])
        self.assertEqual(read_headers(b'"unfinished', 'utf-8-sig', ','), [])
        self.assertEqual(read_headers(b'\xff', 'utf-8-sig', ','), [])

    def test_old_result_without_snapshot_can_resolve_headers(self):
        wizard = self.wizard(b'n\nbad', [{'column_name': 'n', 'data_type': 'integer'}])
        wizard.sudo().header_snapshot = False
        self.assertEqual(wizard.issue_ids.column_label, '1 — n')

    def test_report_is_rectangular_bom_and_formula_safe(self):
        wizard = self.wizard(b'=name\n""', [{'column_name': '=name', 'required': True}])
        data = base64.b64decode(wizard.report_data)
        self.assertTrue(data.startswith(b'\xef\xbb\xbf'))
        rows = list(csv.reader(io.StringIO(data.decode('utf-8-sig'))))
        self.assertTrue(all(len(row) == len(rows[0]) for row in rows))
        self.assertEqual(self.report_rows(wizard)[0]['column_name'], "'=name")
        self.assertEqual(self.report_rows(wizard)[-1]['code'], 'SUMMARY')
        self.assertIn('complete=True', rows[-1][4])

    def test_unknown_extension_code_and_out_of_range_column(self):
        display = describe_issue(self.env, Issue('CUSTOM', 'Custom diagnostic', column=4), ['n'])
        self.assertEqual(display['user_message'], 'Custom diagnostic')
        self.assertEqual(display['issue_type'], 'other')
        self.assertEqual(display['column_label'], '4 — Unknown column')

    def test_presentation_does_not_change_engine_result(self):
        rule = ColumnRule('n', required=True, unique=True, kind='decimal', minimum='0')
        result = validate_csv(b'n\nbad\n-1\n2\n2', column_rules=(rule,))
        before = deepcopy(result)
        snapshot = {'rules': [asdict(rule)], 'decimal_separator': '.'}
        presented = [describe_issue(self.env, issue, ['n'], snapshot) for issue in result.issues]
        export_presented_report(result, presented)
        self.assertEqual(result, before)

    def test_no_issue_report_and_partial_reports(self):
        for data, complete in ((b'n\n1', True), (b'n\n"unfinished', False)):
            wizard = self.wizard(data)
            rows = self.report_rows(wizard)
            self.assertEqual(rows[-1]['code'], 'SUMMARY')
            self.assertIn(f'complete={complete}', rows[-1]['message'])

    def test_catalan_messages_labels_and_report(self):
        self.env['res.lang']._activate_lang('ca_ES')
        self.env['ir.module.module']._load_module_terms(['odoo_csv_validator'], ['ca_ES'], overwrite=True)
        wizard = self.wizard(b'n\nbad', [{'column_name': 'n', 'data_type': 'decimal'}])
        catalan = wizard.with_context(lang='ca_ES')
        self.assertEqual(catalan.issue_ids.user_message,
                         'Introdueix un nombre decimal vàlid (utilitza «.» com a separador decimal).')
        self.assertIn('notació científica', catalan.issue_ids.restriction_details)
        # Stored downloads use the language at validation time; live text follows context.
        catalan.action_validate()
        self.assertEqual(self.report_rows(catalan)[0]['message'], catalan.issue_ids.user_message)
        unnamed = self.wizard(b'n, \n1,2').with_context(lang='ca_ES')
        self.assertEqual(unnamed.issue_ids.column_label, '2 — Columna sense nom')
        self.assertEqual(unnamed.issue_ids.user_message, 'Posa un nom a aquesta columna.')

    def test_resource_syntax_configuration_and_limits_keep_details(self):
        samples = [
            Issue('RECORD_LIMIT', 'Analysis stopped after 100000 data records.'),
            Issue('CSV_SYNTAX', 'Invalid CSV quoting or field exceeds the parser field limit.'),
            Issue('RULE_RESOURCE_LIMIT', 'Uniqueness resource limit reached; analysis stopped.'),
            Issue('RULE_CONFIGURATION', 'A profile may configure at most 100 columns.'),
            Issue('RULE_CONFIGURATION', 'Configured column "n" is ambiguous (duplicate headers).'),
        ]
        displays = [describe_issue(self.env, issue, ['n']) for issue in samples]
        self.assertIn('100000', displays[0]['restriction_details'])
        self.assertIn(f'{csv.field_size_limit():,}', displays[1]['restriction_details'])
        self.assertIn('100,000', displays[2]['restriction_details'])
        self.assertIn('32 MiB', displays[2]['restriction_details'])
        self.assertEqual(displays[3]['user_message'], 'A profile can contain at most 100 column rules.')
        self.assertIn('appears more than once', displays[4]['user_message'])


    def test_memory_limits_have_localized_structure_messages(self):
        for data, code, fragment in [(b',' * 1000, 'COLUMN_LIMIT', '1.000'),
                                     (b'x' * 65537, 'HEADER_SIZE_LIMIT', '64 KiB')]:
            with self.subTest(code=code):
                wizard = self.wizard(data)
                self.assertEqual(wizard.state, 'incomplete')
                self.assertEqual(wizard.issue_ids.code, code)
                self.assertEqual(wizard.issue_ids.issue_type, 'structure')
                self.assertFalse(wizard.header_snapshot)
                self.assertIn(code, self.report_rows(wizard)[0]['code'])
                self.env['res.lang']._activate_lang('ca_ES')
                self.env['ir.module.module']._load_module_terms(['odoo_csv_validator'], ['ca_ES'], overwrite=True)
                self.assertIn(fragment, wizard.with_context(lang='ca_ES').issue_ids.user_message)

    def test_header_presentation_rejects_oversized_headers(self):
        for data in (b',' * 1000, b'x' * 65537, b'"' + b'x' * 65535 + b'\n"'):
            self.assertEqual(read_headers(data, 'utf-8-sig', ','), [])
        self.assertEqual(len(read_headers(','.join(str(i) for i in range(1000)).encode(), 'utf-8-sig', ',')), 1000)


    def test_nul_headers_are_never_read_and_message_is_localized(self):
        from unittest.mock import patch
        for data in (b'a\x00b\n1', b'a\nvalue\x00'):
            with patch('odoo.addons.odoo_csv_validator.services.issue_presentation.csv.reader') as reader:
                self.assertEqual(read_headers(data, 'utf-8-sig', ','), [])
            reader.assert_not_called()
            wizard = self.wizard(data)
            self.assertEqual(wizard.state, 'incomplete')
            self.assertFalse(wizard.header_snapshot)
            self.assertEqual(wizard.issue_ids.issue_type, 'structure')
            self.assertIn('byte offset', wizard.issue_ids.restriction_details)
            self.env['res.lang']._activate_lang('ca_ES')
            self.env['ir.module.module']._load_module_terms(['odoo_csv_validator'], ['ca_ES'], overwrite=True)
            self.assertIn('caràcter NUL', wizard.with_context(lang='ca_ES').issue_ids.user_message)
            self.assertIn('posició de byte', wizard.with_context(lang='ca_ES').issue_ids.restriction_details)
