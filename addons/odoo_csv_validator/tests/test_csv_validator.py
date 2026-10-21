"""Also runnable without Odoo using unittest discovery in this directory."""
import base64
import csv
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

# Load the pure engine without importing the addon package (which loads Odoo).
spec = importlib.util.spec_from_file_location('csv_validator_engine', Path(__file__).parents[1] / 'services' / 'csv_validator.py')
engine = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = engine
spec.loader.exec_module(engine)


class TestCsvValidator(unittest.TestCase):
    def test_utf8_bom_and_original_unchanged(self):
        data = b'\xef\xbb\xbfname,value\r\nJoan,1\r\n'
        original = bytes(data)
        result = engine.validate_csv(data)
        self.assertEqual(result.records, 1)
        self.assertEqual(result.issue_count, 0)
        self.assertEqual(data, original)

    def test_encoding_error_location_and_explicit_cp1252(self):
        data = b'name\r\nAndr\xe9\r\n'
        result = engine.validate_csv(data)
        self.assertFalse(result.complete)
        self.assertEqual(result.issues[0].line, 2)
        self.assertIn('byte offset', result.issues[0].message)
        self.assertEqual(engine.validate_csv(data, 'cp1252').records, 1)

    def test_empty_and_header_only(self):
        self.assertEqual(engine.validate_csv(b'').issues[0].code, 'EMPTY_FILE')
        self.assertEqual(engine.validate_csv(b'a,b').records, 0)
        self.assertEqual(engine.validate_csv(b'\na,b').issues[0].code, 'EMPTY_HEADER')

    def test_header_normalization(self):
        result = engine.validate_csv(b'name, name ,Name, ,\n1,2,3,4,5')
        self.assertEqual([(i.code, i.column) for i in result.issues],
                         [('DUPLICATE_HEADER', 2), ('EMPTY_HEADER', 4), ('EMPTY_HEADER', 5)])

    def test_column_count_blank_records_and_multiline(self):
        result = engine.validate_csv(b'a,b\n\n"one\ntwo",3,4\n1\n,\n')
        self.assertEqual(result.records, 3)
        self.assertEqual(result.blank_lines, 1)
        self.assertEqual([(i.line, i.end_line) for i in result.issues], [(3, 4), (5, 5)])

    def test_separators_and_escaped_quotes(self):
        for delimiter in (',', ';', '\t'):
            data = f'a{delimiter}b\n"a{delimiter}b"{delimiter}"say ""yes"""\n'.encode()
            self.assertEqual(engine.validate_csv(data, delimiter=delimiter).issue_count, 0)

    def test_invalid_quoting_is_partial(self):
        result = engine.validate_csv(b'a,b\n1,2\n"unterminated')
        self.assertFalse(result.complete)
        self.assertEqual(result.records, 1)
        self.assertEqual(result.issues[0].code, 'CSV_SYNTAX')
        self.assertEqual(result.issues[0].line, 3)

    def test_long_field_is_controlled(self):
        data = b'a\n' + b'x' * (csv.field_size_limit() + 1)
        self.assertFalse(engine.validate_csv(data).complete)

    def test_issue_limit_keeps_counting(self):
        result = engine.validate_csv(b'a,b\n1\n2\n3\n', max_issues=2)
        self.assertEqual(result.records, 3)
        self.assertEqual(result.issue_count, 3)
        self.assertEqual(len(result.issues), 2)
        self.assertTrue(result.complete)

    def test_record_limit_exact_and_exceeded(self):
        self.assertTrue(engine.validate_csv(b'a\n1\n2\n', max_records=2).complete)
        result = engine.validate_csv(b'a\n1\n2\n3', max_records=2)
        self.assertFalse(result.complete)
        self.assertEqual(result.records, 2)
        self.assertEqual(result.issues[0].line, 4)

    def test_file_and_base64_limits(self):
        self.assertFalse(engine.validate_csv(b'abc', max_bytes=2).complete)
        for data in (b'', b'!!!!', b'a' * (4 * ((engine.MAX_BYTES + 2) // 3) + 1)):
            with self.assertRaises(ValueError):
                engine.decode_upload(data)
        self.assertEqual(engine.decode_upload(base64.b64encode(b'a,b')), b'a,b')

    def test_options_are_restricted(self):
        for kwargs in ({'encoding': 'latin1'}, {'delimiter': '|'}, {'max_issues': 0}, {'max_records': 100001}):
            with self.assertRaises(ValueError):
                engine.validate_csv(b'a', **kwargs)

    def test_extension_rules_and_safe_report(self):
        def rule(headers, row, start, end):
            yield engine.Issue('=unsafe', '@formula', start, end)
        result = engine.validate_csv(b'a\n1', rules=(rule,))
        exported = engine.export_report(result)
        self.assertTrue(exported.startswith(b'\xef\xbb\xbf'))
        rows = list(csv.reader(io.StringIO(exported.decode('utf-8-sig'))))
        self.assertEqual(rows[1][0], "'=unsafe")
        self.assertEqual(rows[1][4], "'@formula")
        self.assertIn('complete=True', rows[-1][4])

    def test_report_marks_truncation_and_partial_results(self):
        result = engine.validate_csv(b'a,b\n1\n2\n3', max_records=2, max_issues=1)
        report = engine.export_report(result).decode('utf-8-sig')
        self.assertIn('complete=False', report)
        self.assertIn('details_truncated=True', report)


class TestCsvMemoryLimits(unittest.TestCase):
    def assert_limit(self, data, code, **options):
        result = engine.validate_csv(data, **options)
        self.assertFalse(result.complete)
        self.assertEqual(result.issues[-1].code, code)
        return result

    def test_exact_column_limit_all_delimiters(self):
        for delimiter in engine.DELIMITERS:
            with self.subTest(delimiter=delimiter):
                header = delimiter.join(f'c{i}' for i in range(1000))
                row = delimiter.join('x' for _ in range(1000))
                result = engine.validate_csv((header + '\n' + row).encode(), delimiter=delimiter)
                self.assertTrue(result.complete)
                self.assertEqual((result.records, result.issue_count), (1, 0))

    def test_header_column_limit(self):
        for delimiter in engine.DELIMITERS:
            self.assert_limit((delimiter * 1000).encode(), 'COLUMN_LIMIT', delimiter=delimiter)

    def test_data_column_limit_keeps_previous_records(self):
        for ending in ('\n', '\r', '\r\n'):
            result = self.assert_limit(('a' + ending + 'ok' + ending + ',' * 1000).encode(), 'COLUMN_LIMIT')
            self.assertEqual(result.records, 1)
            self.assertEqual((result.issues[-1].line, result.issues[-1].end_line), (3, 3))

    def test_header_byte_boundary_and_terminators(self):
        for ending in (b'', b'\n', b'\r', b'\r\n'):
            with self.subTest(ending=ending):
                self.assertEqual(engine.validate_csv(b'a' * 65536 + ending).issue_count, 0)
                self.assert_limit(b'a' * 65537 + ending, 'HEADER_SIZE_LIMIT')

    def test_header_bytes_utf8_bom_and_cp1252(self):
        self.assertEqual(engine.validate_csv(b'\xef\xbb\xbf' + ('é' * 32768).encode()).issue_count, 0)
        self.assert_limit(('é' * 32768 + 'a').encode(), 'HEADER_SIZE_LIMIT')
        self.assertEqual(engine.validate_csv(b'\xe9' * 65536, encoding='cp1252').issue_count, 0)
        self.assert_limit(b'\xe9' * 65537, 'HEADER_SIZE_LIMIT', encoding='cp1252')

    def test_header_size_counts_quotes_and_embedded_newlines(self):
        for ending in ('\n', '\r', '\r\n'):
            header = '"' + 'x' * (65534 - len(ending)) + ending + '"'
            self.assertEqual(engine.validate_csv(header.encode()).issue_count, 0)
            result = self.assert_limit((header[:-1] + 'x"').encode(), 'HEADER_SIZE_LIMIT')
            self.assertEqual((result.issues[-1].line, result.issues[-1].end_line), (1, 2))

    def test_quoted_delimiters_escaped_quotes_and_multiline(self):
        for delimiter in engine.DELIMITERS:
            field = '"' + delimiter * 2000 + '\r\n' + '""quoted""' + '"'
            data = (field + delimiter + 'b\n' + field + delimiter + 'x').encode()
            result = engine.validate_csv(data, delimiter=delimiter)
            self.assertEqual((result.complete, result.records, result.issue_count), (True, 1, 0))

    def test_multiline_data_limit_locations(self):
        result = self.assert_limit(b'a\n"multi\nline",' + b',' * 999, 'COLUMN_LIMIT')
        self.assertEqual((result.issues[0].line, result.issues[0].end_line), (2, 3))
        self.assertEqual(result.records, 0)

    def test_data_is_not_subject_to_header_size_limit(self):
        result = engine.validate_csv(b'a\n' + b'x' * 65537)
        self.assertEqual((result.complete, result.records, result.issue_count), (True, 1, 0))

    def test_blank_records_reset_column_count(self):
        result = engine.validate_csv(b'a\n\n1\n\n2\n')
        self.assertEqual((result.records, result.blank_lines, result.issue_count), (2, 2, 0))

    def test_quote_in_unquoted_field_and_strict_syntax_unchanged(self):
        self.assertEqual(engine.validate_csv(b'a,b\nx"y,z').issue_count, 0)
        result = engine.validate_csv(b'a\n"x"bad')
        self.assertEqual(result.issues[0].code, 'CSV_SYNTAX')
        self.assertEqual((result.issues[0].line, result.issues[0].end_line), (2, 2))

    def test_rejected_physical_line_never_reaches_csv_reader(self):
        for text, code in [('a,' * 100000, 'COLUMN_LIMIT'), ('x' * 65537, 'HEADER_SIZE_LIMIT')]:
            with self.subTest(code=code), patch.object(engine.csv, 'reader') as reader:
                reader.side_effect = lambda lines, **kw: iter(lines)
                result = self.assert_limit(text.encode(), code)
                self.assertEqual(result.records, 0)

    def test_guard_stops_reading_immediately(self):
        # No parser or field list is needed to reject the first physical line.
        lines = engine.bounded_csv_lines('a\n' + ',' * 1000000, 'utf-8-sig', ',')
        self.assertEqual(next(lines), 'a\n')
        with self.assertRaises(engine.CsvResourceLimit) as error:
            next(lines)
        self.assertEqual(error.exception.code, 'COLUMN_LIMIT')

    def test_resource_issue_still_counts_when_details_truncated(self):
        result = self.assert_limit(b'a,b\n1\n' + b',' * 1000, 'COLUMN_COUNT', max_issues=1)
        self.assertEqual((result.issue_count, len(result.issues), result.records), (2, 1, 1))
        self.assertIn(b'complete=False', engine.export_report(result))


class TestCsvNul(unittest.TestCase):
    def test_nul_locations_use_source_bytes_and_physical_lines(self):
        cases = [(b'\x00', 0, 1), (b'a\x00b\n1', 1, 1),
                 (b'a\n1\x00', 3, 2), (b'a\r1\x00', 3, 2),
                 (b'a\r\n"multi\r\nline\x00"', 15, 3),
                 (b'\xef\xbb\xbf' + 'é\n'.encode() + b'\x00', 6, 2),
                 (b'a\r\x00\n', 2, 2)]
        for data, offset, line in cases:
            with self.subTest(data=data):
                result = engine.validate_csv(data)
                self.assertFalse(result.complete)
                self.assertEqual((result.records, result.issue_count), (0, 1))
                issue = result.issues[0]
                self.assertEqual(issue.code, 'NUL_CHARACTER')
                self.assertEqual((issue.line, issue.end_line, issue.column), (line, line, 0))
                self.assertIn(f'offset {offset} (zero-based)', issue.message)
                self.assertNotIn('\x00', issue.message)

    def test_nul_rejected_before_decoder_parser_or_extension(self):
        data = b'a\n\xff\x00'
        with patch.object(engine.csv, 'reader') as reader:
            rule = unittest.mock.Mock()
            result = engine.validate_csv(data, rules=(rule,))
        self.assertEqual(result.issues[0].code, 'NUL_CHARACTER')
        reader.assert_not_called()
        rule.assert_not_called()

    def test_nul_supported_encodings_and_delimiters(self):
        for encoding in engine.ENCODINGS:
            for delimiter in engine.DELIMITERS:
                result = engine.validate_csv(('a' + delimiter + 'b\n"x\x00y"' + delimiter + 'z').encode(),
                                             encoding, delimiter)
                self.assertEqual(result.issues[0].code, 'NUL_CHARACTER')
                self.assertFalse(result.complete)

    def test_first_nul_only_and_original_bytes_preserved(self):
        data = b'a\n\x00\n\x00'
        result = engine.validate_csv(data)
        self.assertEqual(data, b'a\n\x00\n\x00')
        self.assertEqual((result.issue_count, result.issues[0].line), (1, 2))
        report = engine.export_report(result)
        self.assertIn(b'NUL_CHARACTER', report)
        self.assertNotIn(b'\x00', report)

    def test_utf16_nuls_report_encoding_hint(self):
        result = engine.validate_csv('a\n1'.encode('utf-16-le'))
        self.assertEqual(result.issues[0].code, 'NUL_CHARACTER')
        self.assertIn('encoding', result.issues[0].message)

    def test_file_size_limit_still_precedes_nul_scan(self):
        result = engine.validate_csv(b'abc\x00', max_bytes=3)
        self.assertEqual(result.issues[0].code, 'FILE_TOO_LARGE')
