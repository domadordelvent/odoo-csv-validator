"""Read-only presentation adapter: no validation decisions belong here.

All user-facing templates are English literals through env._, so Odoo can
extract them for any language. Unknown addon codes retain their original text.
"""
import csv
import io
import re

from .csv_validator import bounded_csv_lines, CsvResourceLimit, nul_issue
from .column_rules import MAX_NUMBER_LENGTH, MAX_UNIQUE_BYTES, MAX_UNIQUE_COLUMNS, MAX_UNIQUE_KEYS

STRUCTURE_CODES = frozenset({
    'EMPTY_FILE', 'EMPTY_HEADER', 'DUPLICATE_HEADER', 'COLUMN_COUNT',
    'CSV_SYNTAX', 'ENCODING_ERROR', 'FILE_TOO_LARGE', 'RECORD_LIMIT',
    'COLUMN_LIMIT', 'HEADER_SIZE_LIMIT', 'NUL_CHARACTER',
})
CONTENT_CODES = frozenset({
    'REQUIRED', 'DUPLICATE_VALUE', 'INVALID_NUMBER', 'BELOW_MINIMUM',
    'RULE_COLUMN_MISSING', 'RULE_CONFIGURATION', 'RULE_RESOURCE_LIMIT',
})


def upload_message(env, message):
    return {
        'Select a CSV file first.': env._('Choose a CSV file first.'),
        'The file exceeds the 10 MiB limit.': env._('The file is too large. Maximum file size: 10 MiB.'),
        'The uploaded file is not valid base64.': env._('The upload could not be read. Upload the file again.'),
    }.get(message, message)


def configuration_message(env, message):
    return {
        'A profile may configure at most 100 columns.': env._('A profile can contain at most 100 column rules.'),
        'A profile may configure at most 20 unique columns.': env._('Uniqueness can be checked on at most 20 columns.'),
        'Column names must be nonempty, distinct and at most 256 characters.': env._('Use a different, nonempty column name for each rule (maximum 256 characters).'),
        'Unknown column type.': env._('Choose text, integer or decimal as the column type.'),
        'A minimum requires an integer or decimal column.': env._('Choose integer or decimal before setting a minimum.'),
        'Unsupported decimal separator.': env._('Choose a point or comma as the decimal separator.'),
        'Numeric value exceeds 1,024 characters.': env._('The minimum cannot exceed 1,024 characters.'),
        'Invalid numeric syntax.': env._('Enter a valid minimum using a decimal point, without scientific notation or thousands separators.'),
        'Invalid decimal.': env._('Enter a valid decimal minimum.'),
        'Numeric value must be finite.': env._('The minimum must be finite; NaN and infinity are not accepted.'),
        'Unsupported numeric format.': env._('Choose a supported numeric format.'),
    }.get(message, message)


def read_headers(data, encoding, delimiter):
    """Use the same CSV dialect; never guess a name after a decoding failure."""
    if nul_issue(data):
        return []
    try:
        reader = csv.reader(bounded_csv_lines(data.decode(encoding), encoding, delimiter), delimiter=delimiter, strict=True)
        return next(reader, [])
    except (UnicodeError, csv.Error, CsvResourceLimit):
        return []


def column_details(env, column, headers):
    if column <= 0:
        return '', env._('Column not identified')
    if column > len(headers):
        return '', env._('%(number)s — Unknown column', number=column)
    name = headers[column - 1].strip()
    return name, (f'{column} — {name}' if name else
                  env._('%(number)s — Unnamed column', number=column))


def describe_issue(env, issue, headers, snapshot=None):
    name, label = column_details(env, issue.column, headers)
    category = ('structure' if issue.code in STRUCTURE_CODES else
                'content' if issue.code in CONTENT_CODES else 'other')
    message, details = issue.message, ''
    snapshot = snapshot or {}
    rule = next((rule for rule in snapshot.get('rules', [])
                 if rule['column'].strip() == name), {})
    if issue.code == 'REQUIRED':
        message = env._('Enter a value.')
    elif issue.code == 'DUPLICATE_VALUE' and issue.first_line:
        message = env._('This value is already used on line %(line)s.', line=issue.first_line)
    elif issue.code == 'INVALID_NUMBER':
        if rule.get('kind') == 'integer' or issue.message.startswith('Expected an integer'):
            message = env._('Enter a valid whole number.')
            details = env._('Use digits 0–9 with an optional + or - sign. Maximum %(limit)s characters. Decimal values, scientific notation and thousands separators are not accepted.', limit=f'{MAX_NUMBER_LENGTH:,}')
        else:
            separator = snapshot.get('decimal_separator')
            old_format = re.fullmatch(r'Expected a finite decimal using "([.,])" .*', issue.message)
            if separator or old_format:
                separator = separator or old_format[1]
                message = env._('Enter a valid decimal number (use "%(separator)s" as the decimal separator).', separator=separator)
                details = env._('Use digits 0–9 with an optional + or - sign. Maximum %(limit)s characters. Scientific notation, thousands separators, NaN and infinity are not accepted.', limit=f'{MAX_NUMBER_LENGTH:,}')
    elif issue.code == 'BELOW_MINIMUM':
        match = re.fullmatch(r'Value must be greater than or equal to (.+)\.', issue.message)
        minimum = rule.get('minimum')
        if minimum is not None or match:
            message = env._('Enter a value of at least %(minimum)s.', minimum=minimum if minimum is not None else match[1])
    elif issue.code == 'EMPTY_FILE':
        message = env._('The file is empty. Add a header row.')
    elif issue.code == 'EMPTY_HEADER':
        message = env._('Give this column a name.') if issue.column else env._('The first row must contain column names.')
    elif issue.code == 'DUPLICATE_HEADER':
        match = re.fullmatch(r'Header duplicates column (\d+)\.', issue.message)
        if match:
            message = env._('This column name repeats column %(column)s.', column=match[1])
    elif issue.code == 'COLUMN_COUNT':
        match = re.fullmatch(r'Expected (\d+) columns; found (\d+)\.', issue.message)
        if match:
            message = env._('Wrong number of columns: %(actual)s instead of %(expected)s.', actual=match[2], expected=match[1])
    elif issue.code == 'COLUMN_LIMIT':
        message = env._('The CSV exceeds 1,000 columns. Remove columns and try again.')
    elif issue.code == 'HEADER_SIZE_LIMIT':
        message = env._('The CSV header exceeds 64 KiB. Shorten the column names.')
    elif issue.code == 'NUL_CHARACTER':
        message = env._('The CSV contains a NUL character. Remove NUL characters or check the file encoding.')
        match = re.fullmatch(r'NUL character at byte offset (\d+) \(zero-based\). .*', issue.message)
        if match:
            details = env._('Problem at byte offset %(offset)s (counting from zero).', offset=match[1])
    elif issue.code == 'CSV_SYNTAX':
        message = env._('The CSV could not be read.')
        details = env._('Check quotation marks. Each field must contain at most %(limit)s characters.', limit=f'{csv.field_size_limit():,}')
    elif issue.code == 'ENCODING_ERROR':
        match = re.fullmatch(r'Invalid (.+) sequence at byte offset (\d+) \(zero-based\)\.', issue.message)
        if match:
            message = env._('The file contains characters that cannot be read using %(encoding)s.', encoding=match[1])
            details = env._('Problem at byte offset %(offset)s (counting from zero). Choose the correct file encoding.', offset=match[2])
    elif issue.code == 'FILE_TOO_LARGE':
        message = env._('Choose a smaller file.')
        details = env._('Maximum file size: 10 MiB.')
    elif issue.code == 'RECORD_LIMIT':
        match = re.fullmatch(r'Analysis stopped after (\d+) data records\.', issue.message)
        if match:
            message = env._('The record limit was reached.')
            details = env._('Only the first %(limit)s data records were checked. The analysis is incomplete.', limit=match[1])
    elif issue.code == 'RULE_COLUMN_MISSING':
        match = re.fullmatch(r'Configured column "(.*)" is missing\.', issue.message, re.S)
        if match:
            message = env._('The CSV has no column named "%(name)s".', name=match[1])
    elif issue.code == 'RULE_CONFIGURATION':
        match = re.fullmatch(r'Configured column "(.*)" is ambiguous \(duplicate headers\)\.', issue.message, re.S)
        if match:
            message = env._('Column "%(name)s" appears more than once in the header.', name=match[1])
        else:
            message = configuration_message(env, issue.message)
    elif issue.code == 'RULE_RESOURCE_LIMIT':
        message = env._('The uniqueness limit was reached.')
        details = env._('Analysis stopped. Limits: %(keys)s unique values across at most %(columns)s columns and %(memory)s MiB of estimated storage.',
                        keys=f'{MAX_UNIQUE_KEYS:,}', columns=MAX_UNIQUE_COLUMNS,
                        memory=MAX_UNIQUE_BYTES // (1024 * 1024))
    return {'column_name': name, 'column_label': label, 'issue_type': category,
            'user_message': message, 'restriction_details': details}


def export_presented_report(result, presented):
    """Stable diagnostic columns plus human-readable, localized presentation."""
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['code', 'start_line', 'end_line', 'column', 'message',
                     'column_name', 'issue_type', 'restrictions', 'technical_message',
                     'first_occurrence_line'])

    def safe(value):
        value = str(value)
        return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value

    for issue, display in zip(result.issues, presented):
        writer.writerow([safe(issue.code), issue.line or '', issue.end_line or '',
                         issue.column or '', safe(display['user_message']),
                         safe(display['column_name']), display['issue_type'],
                         safe(display['restriction_details']), safe(issue.message),
                         issue.first_line or ''])
    writer.writerow(['SUMMARY', '', '', '',
                     f'records={result.records}; blank_lines={result.blank_lines}; issues={result.issue_count}; complete={result.complete}; details_truncated={result.issue_count > len(result.issues)}',
                     '', '', '', '', ''])
    return stream.getvalue().encode('utf-8-sig')
