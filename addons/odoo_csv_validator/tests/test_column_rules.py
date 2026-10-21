"""Pure Python content tests, runnable without importing Odoo."""
import csv
import importlib
import io
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

package_name = 'csv_validator_pure_tests'
if package_name not in sys.modules:
    package = types.ModuleType(package_name)
    package.__path__ = [str(Path(__file__).parents[1] / 'services')]
    sys.modules[package_name] = package
engine = importlib.import_module(package_name + '.csv_validator')
content = importlib.import_module(package_name + '.column_rules')
Rule = content.ColumnRule


class TestColumnRules(unittest.TestCase):
    def run_csv(self, text, rules, **options):
        return engine.validate_csv(text.encode(), column_rules=tuple(rules), **options)

    def codes(self, result):
        return [issue.code for issue in result.issues]

    def test_contacts_required_and_optional_blanks(self):
        result = self.run_csv('name,email\n ,\nAda,\n', [Rule('name', required=True), Rule('email', unique=True)])
        self.assertEqual(self.codes(result), ['REQUIRED'])
        self.assertEqual((result.issues[0].line, result.issues[0].column), (2, 1))
        self.assertEqual(result.records, 2)

    def test_text_unique_trim_case_sensitive_and_zero_prefix(self):
        result = self.run_csv('id\n001\n1\n A\nA \na\n', [Rule('id', unique=True)])
        self.assertEqual(self.codes(result), ['DUPLICATE_VALUE'])
        self.assertEqual(result.issues[0].first_line, 4)

    def test_disable_trimming(self):
        result = self.run_csv('x\nA\n A\n \n', [Rule('x', required=True, unique=True)], trim_whitespace=False)
        self.assertEqual(result.issue_count, 0)

    def test_integer_syntax(self):
        result = self.run_csv('count\n-1\n+2\n003\n1.0\n1e2\nNaN\n1,000\n', [Rule('count', kind='integer')])
        self.assertEqual(self.codes(result), ['INVALID_NUMBER', 'INVALID_NUMBER', 'INVALID_NUMBER', 'COLUMN_COUNT'])

    def test_decimal_syntax_and_nonfinite_values(self):
        result = self.run_csv('amount\n-1.25\n+.5\n2\nNaN\nInfinity\n1e2\n1.\n1_000\n', [Rule('amount', kind='decimal')])
        self.assertEqual(self.codes(result), ['INVALID_NUMBER'] * 5)

    def test_finance_decimal_comma_and_exact_minimum(self):
        result = self.run_csv('amount;description\n0,10000000000000000001;ok\n0,1;small\n',
                              [Rule('amount', kind='decimal', minimum='0.10000000000000000001')],
                              delimiter=';', decimal_separator=',')
        self.assertEqual(self.codes(result), ['BELOW_MINIMUM'])
        self.assertEqual(result.issues[0].line, 3)

    def test_combined_integer_rules(self):
        result = self.run_csv('n\n""\nabc\n-1\n2\n2\n',
                              [Rule('n', required=True, unique=True, kind='integer', minimum='0')])
        self.assertEqual(self.codes(result), ['REQUIRED', 'INVALID_NUMBER', 'BELOW_MINIMUM', 'DUPLICATE_VALUE'])
        self.assertEqual(result.issues[-1].first_line, 5)

    def test_numeric_uniqueness_and_per_run_state(self):
        rules = [Rule('n', unique=True, kind='decimal')]
        for _ in range(2):
            result = self.run_csv('n\n1\n1.0\n+01.00\n-0\n0\n', rules)
            self.assertEqual(self.codes(result), ['DUPLICATE_VALUE'] * 3)
            self.assertEqual([i.first_line for i in result.issues], [2, 2, 5])

    def test_empty_optional_numeric_skips_minimum(self):
        result = self.run_csv('a,b\n ,x\n,x\n', [Rule('a', unique=True, kind='decimal', minimum='5')])
        self.assertEqual(result.issue_count, 0)

    def test_missing_column_header_only_is_reported(self):
        result = self.run_csv('a,b\n', [Rule('missing', required=True)])
        self.assertEqual(self.codes(result), ['RULE_COLUMN_MISSING'])
        self.assertFalse(result.complete)
        self.assertEqual(result.issues[0].line, 1)

    def test_ambiguous_headers_do_not_guess(self):
        result = self.run_csv('a, a \n1,2\n', [Rule('a', kind='integer')])
        self.assertEqual(self.codes(result), ['DUPLICATE_HEADER', 'RULE_CONFIGURATION'])
        self.assertFalse(result.complete)

    def test_rule_configuration_validation(self):
        for rules in ([Rule(' ')], [Rule('a'), Rule(' a ')], [Rule('a', kind='expression')],
                      [Rule('a', minimum='0')], [Rule('a', kind='decimal', minimum='NaN')],
                      [Rule(str(i), unique=True) for i in range(21)], [Rule(str(i)) for i in range(101)]):
            with self.subTest(rules=rules):
                result = self.run_csv('a\n1', rules)
                self.assertEqual(self.codes(result), ['RULE_CONFIGURATION'])
                self.assertFalse(result.complete)

    def test_bad_width_and_multiline_locations(self):
        result = self.run_csv('name,n\n"multi\nline",bad\nonlyone\n', [Rule('n', kind='integer')])
        self.assertEqual(self.codes(result), ['INVALID_NUMBER', 'COLUMN_COUNT'])
        self.assertEqual((result.issues[0].line, result.issues[0].end_line, result.issues[0].column), (2, 3, 2))

    def test_issue_limit_keeps_content_counting(self):
        result = self.run_csv('n\nx\ny\nz\n', [Rule('n', kind='integer')], max_issues=1)
        self.assertEqual((result.issue_count, len(result.issues), result.records), (3, 1, 3))

    def test_unique_limit_stops_even_when_details_are_full(self):
        with patch.object(content, 'MAX_UNIQUE_KEYS', 1):
            result = self.run_csv('n\n1\n1\n2\n3\n', [Rule('n', unique=True)], max_issues=1)
        self.assertFalse(result.complete)
        self.assertEqual((result.records, result.issue_count), (3, 2))

    def test_unique_memory_budget(self):
        with patch.object(content, 'MAX_UNIQUE_BYTES', 1):
            result = self.run_csv('n\n1\n2', [Rule('n', unique=True)])
        self.assertEqual(self.codes(result), ['RULE_RESOURCE_LIMIT'])
        self.assertFalse(result.complete)

    def test_numbers_are_bounded(self):
        result = self.run_csv('n\n' + '1' * 1025, [Rule('n', kind='integer')])
        self.assertEqual(self.codes(result), ['INVALID_NUMBER'])

    def test_report_duplicate_metadata_and_formula_safety(self):
        result = self.run_csv('code\n=SUM(1)\n=SUM(1)', [Rule('code', unique=True)])
        report = engine.export_report(result).decode('utf-8-sig')
        rows = list(csv.reader(io.StringIO(report)))
        self.assertEqual(rows[0][-1], 'first_occurrence_line')
        self.assertEqual(rows[1][-1], '2')
        self.assertNotIn('=SUM', report)

    def test_without_profile_is_unchanged(self):
        original = engine.validate_csv(b'n\nx\n-1\nx\n')
        empty = engine.validate_csv(b'n\nx\n-1\nx\n', column_rules=())
        self.assertEqual(original, empty)
        self.assertEqual(engine.export_report(original), engine.export_report(empty))

    def test_structural_stops_remain_incomplete(self):
        for text, options in [('n\n1\n2\n3', {'max_records': 2}), ('n\n1\n"unterminated', {})]:
            result = self.run_csv(text, [Rule('n', unique=True)], **options)
            self.assertFalse(result.complete)
