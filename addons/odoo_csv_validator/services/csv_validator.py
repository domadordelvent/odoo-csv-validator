"""Pure Python CSV validation. Never changes the supplied bytes.

Optional declarative column rules have per-run state. Trusted callable hooks
remain supported for addon extensions independently of Odoo.
"""
import base64
import binascii
import csv
import io
from dataclasses import dataclass, field

MAX_BYTES = 10 * 1024 * 1024
MAX_RECORDS = 100_000
MAX_ISSUES = 1_000
MAX_COLUMNS = 1_000
MAX_HEADER_BYTES = 64 * 1024
ENCODINGS = ('utf-8-sig', 'cp1252')
DELIMITERS = (',', ';', '\t')


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    line: int = 0
    end_line: int = 0
    column: int = 0
    first_line: int = 0


@dataclass
class Result:
    records: int = 0
    blank_lines: int = 0
    issue_count: int = 0
    complete: bool = True
    issues: list = field(default_factory=list)

    def add(self, issue, limit):
        self.issue_count += 1
        if len(self.issues) < limit:
            self.issues.append(issue)


def decode_upload(encoded):
    """Reject oversized base64 before allocating the decoded upload."""
    if not encoded:
        raise ValueError('Select a CSV file first.')
    if len(encoded) > 4 * ((MAX_BYTES + 2) // 3):
        raise ValueError('The file exceeds the 10 MiB limit.')
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise ValueError('The uploaded file is not valid base64.') from exc
    if len(data) > MAX_BYTES:
        raise ValueError('The file exceeds the 10 MiB limit.')
    return data


class CsvResourceLimit(ValueError):
    """A logical record exceeded a limit before being passed to csv.reader."""

    def __init__(self, code, message, start, end):
        super().__init__(message)
        self.code, self.start, self.end = code, start, end


def bounded_csv_lines(text, encoding, delimiter):
    """Guard physical lines before csv.reader can materialize their fields.

    Match the default reader dialect: quotes open only at field start,
    doubled quotes escape a quote, and quoted fields can span physical lines.
    Keep only counters and dialect state, never a list of fields. Header bytes
    exclude the BOM and terminating newline, but include CSV syntax and embedded
    newlines. The upload itself remains bounded by MAX_BYTES.
    """
    state = 'start'
    columns = 1
    header = True
    header_bytes = 0
    start = 1
    for number, line in enumerate(io.StringIO(text, newline=''), 1):
        for char in line:
            terminator = char in '\r\n' and state != 'quoted'
            if header and not terminator:
                header_bytes += (len(char.encode('utf-8')) if encoding == 'utf-8-sig' else 1)
                if header_bytes > MAX_HEADER_BYTES:
                    raise CsvResourceLimit('HEADER_SIZE_LIMIT',
                                           'The CSV header exceeds 64 KiB. Shorten the column names.', start, number)
            if terminator:
                continue
            if state == 'quoted':
                if char == '"':
                    state = 'after_quote'
            elif state == 'after_quote' and char == '"':
                state = 'quoted'
            elif char == delimiter:
                columns += 1
                if columns > MAX_COLUMNS:
                    raise CsvResourceLimit('COLUMN_LIMIT',
                                           'The CSV exceeds 1,000 columns. Remove columns and try again.', start, number)
                state = 'start'
            elif state == 'start' and char == '"':
                state = 'quoted'
            elif state == 'after_quote':
                # Preserve strict=True syntax handling before inspecting more input.
                yield line
                return
            else:
                state = 'unquoted'
        if state != 'quoted':
            header = False
            columns = 1
            state = 'start'
            start = number + 1
        yield line


def nul_issue(data):
    """Locate the first NUL in source bytes, before decoding or parsing.

    Both supported encodings encode NUL as a single zero byte. Count physical
    lines without allocating a prefix or a list of every preceding line.
    """
    offset = data.find(b'\x00')
    if offset < 0:
        return None
    line = (1 + data.count(b'\n', 0, offset) + data.count(b'\r', 0, offset)
            - data.count(b'\r\n', 0, offset))
    return Issue('NUL_CHARACTER',
                 f'NUL character at byte offset {offset} (zero-based). Remove NUL characters or check the file encoding.',
                 line, line)


def validate_csv(data, encoding='utf-8-sig', delimiter=',', *,
                 max_bytes=MAX_BYTES, max_records=MAX_RECORDS,
                 max_issues=MAX_ISSUES, rules=(), column_rules=None,
                 trim_whitespace=True, decimal_separator='.'):
    if encoding not in ENCODINGS or delimiter not in DELIMITERS:
        raise ValueError('Unsupported encoding or delimiter.')
    if not 0 < max_bytes <= MAX_BYTES or not 0 < max_records <= MAX_RECORDS or not 0 < max_issues <= MAX_ISSUES:
        raise ValueError('Invalid validation limits.')
    result = Result()
    content = None
    if column_rules is not None:
        from .column_rules import ColumnValidator
        content = ColumnValidator(column_rules, trim_whitespace, decimal_separator)

    def error(code, message, line=0, end_line=0, column=0):
        result.add(Issue(code, message, line, end_line, column), max_issues)

    if len(data) > max_bytes:
        result.complete = False
        error('FILE_TOO_LARGE', 'File exceeds the allowed size.')
        return result
    nul = nul_issue(data)
    if nul:
        result.complete = False
        result.add(nul, max_issues)
        return result
    try:
        text = data.decode(encoding, errors='strict')
    except UnicodeDecodeError as exc:
        result.complete = False
        line = len(data[:exc.start].replace(b'\r\n', b'\n').replace(b'\r', b'\n').split(b'\n'))
        error('ENCODING_ERROR', f'Invalid {encoding} sequence at byte offset {exc.start} (zero-based).', line, line)
        return result
    reader = csv.reader(bounded_csv_lines(text, encoding, delimiter), delimiter=delimiter, strict=True)
    headers = None
    previous_line = 0
    try:
        for row in reader:
            end = reader.line_num
            start = previous_line + 1
            previous_line = end
            if headers is None:
                headers = row
                if not headers:
                    error('EMPTY_HEADER', 'The first record must contain headers.', start, end)
                seen = {}
                for column, name in enumerate(headers, 1):
                    normalized = name.strip()
                    if not normalized:
                        error('EMPTY_HEADER', 'Header is empty or whitespace only.', start, end, column)
                    elif normalized in seen:
                        error('DUPLICATE_HEADER', f'Header duplicates column {seen[normalized]}.', start, end, column)
                    else:
                        seen[normalized] = column
                if content:
                    for issue in content.prepare(tuple(headers), start, end):
                        result.add(issue, max_issues)
                continue
            if not row:
                result.blank_lines += 1
                continue
            if result.records == max_records:
                result.complete = False
                error('RECORD_LIMIT', f'Analysis stopped after {max_records} data records.', start, end)
                break
            result.records += 1
            if len(row) != len(headers):
                error('COLUMN_COUNT', f'Expected {len(headers)} columns; found {len(row)}.', start, end)
            elif content:
                for issue in content.validate(tuple(row), start, end):
                    result.add(issue, max_issues)
                if content.exhausted:
                    break
            for rule in rules:
                for issue in rule(tuple(headers), tuple(row), start, end):
                    result.add(issue, max_issues)
    except CsvResourceLimit as exc:
        result.complete = False
        error(exc.code, str(exc), exc.start, exc.end)
    except csv.Error:
        result.complete = False
        error('CSV_SYNTAX', 'Invalid CSV quoting or field exceeds the parser field limit.',
              previous_line + 1, reader.line_num)
    if headers is None and result.complete:
        error('EMPTY_FILE', 'The file does not contain a header record.')
    if content and content.incomplete:
        result.complete = False
    return result


def export_report(result):
    """Export bounded details and summary; neutralize spreadsheet formulas."""
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    with_first_line = any(issue.first_line for issue in result.issues)
    writer.writerow(['code', 'start_line', 'end_line', 'column', 'message'] + (['first_occurrence_line'] if with_first_line else []))

    def safe(value):
        value = str(value)
        return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value

    for issue in result.issues:
        writer.writerow([safe(issue.code), issue.line or '', issue.end_line or '', issue.column or '', safe(issue.message)] + ([issue.first_line or ''] if with_first_line else []))
    writer.writerow(['SUMMARY', '', '', '',
                     f'records={result.records}; blank_lines={result.blank_lines}; issues={result.issue_count}; complete={result.complete}; details_truncated={result.issue_count > len(result.issues)}'] + ([''] if with_first_line else []))
    return stream.getvalue().encode('utf-8-sig')
