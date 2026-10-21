"""Declarative column rules. No ORM, eval, expressions or shared run state."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
import sys

from .csv_validator import Issue

MAX_RULES = 100
MAX_UNIQUE_COLUMNS = 20
MAX_UNIQUE_KEYS = 100_000
MAX_UNIQUE_BYTES = 32 * 1024 * 1024
MAX_NUMBER_LENGTH = 1024


@dataclass(frozen=True)
class ColumnRule:
    column: str
    required: bool = False
    unique: bool = False
    kind: str = 'text'
    minimum: str | None = None


def parse_number(value, kind='decimal', separator='.'):
    if kind not in ('integer', 'decimal') or separator not in ('.', ','):
        raise ValueError('Unsupported numeric format.')
    if len(value) > MAX_NUMBER_LENGTH:
        raise ValueError('Numeric value exceeds 1,024 characters.')
    point = re.escape(separator)
    pattern = (r'[+-]?[0-9]+' if kind == 'integer' else
               rf'[+-]?(?:[0-9]+(?:{point}[0-9]+)?|{point}[0-9]+)')
    if not re.fullmatch(pattern, value):
        raise ValueError('Invalid numeric syntax.')
    try:
        number = Decimal(value.replace(separator, '.'))
    except InvalidOperation as exc:
        raise ValueError('Invalid decimal.') from exc
    if not number.is_finite():
        raise ValueError('Numeric value must be finite.')
    return number


def check_rules(rules):
    """Validate trusted configuration too, independently of Odoo constraints."""
    if len(rules) > MAX_RULES:
        raise ValueError('A profile may configure at most 100 columns.')
    if sum(bool(rule.unique) for rule in rules) > MAX_UNIQUE_COLUMNS:
        raise ValueError('A profile may configure at most 20 unique columns.')
    names = set()
    for rule in rules:
        name = rule.column.strip()
        if not name or len(name) > 256 or name in names:
            raise ValueError('Column names must be nonempty, distinct and at most 256 characters.')
        names.add(name)
        if rule.kind not in ('text', 'integer', 'decimal'):
            raise ValueError('Unknown column type.')
        if rule.minimum is not None:
            if rule.kind == 'text':
                raise ValueError('A minimum requires an integer or decimal column.')
            # Thresholds use a canonical point, regardless of CSV decimal separator.
            parse_number(rule.minimum, 'decimal', '.')


class ColumnValidator:
    def __init__(self, rules, trim_whitespace=True, decimal_separator='.'):
        self.rules = tuple(rules)
        self.trim_whitespace = trim_whitespace
        self.decimal_separator = decimal_separator
        self.bound = []
        self.seen = {}
        self.key_count = 0
        self.key_bytes = 0
        self.incomplete = False
        self.exhausted = False

    def prepare(self, headers, start, end):
        try:
            check_rules(self.rules)
            if self.decimal_separator not in ('.', ','):
                raise ValueError('Unsupported decimal separator.')
        except ValueError as exc:
            self.incomplete = True
            yield Issue('RULE_CONFIGURATION', str(exc), start, end)
            return
        positions = {}
        for column, header in enumerate(headers, 1):
            positions.setdefault(header.strip(), []).append(column)
        for rule in self.rules:
            columns = positions.get(rule.column.strip(), [])
            if len(columns) != 1:
                self.incomplete = True
                yield Issue('RULE_COLUMN_MISSING' if not columns else 'RULE_CONFIGURATION',
                            f'Configured column "{rule.column.strip()}" is missing.' if not columns else
                            f'Configured column "{rule.column.strip()}" is ambiguous (duplicate headers).',
                            start, end, columns[0] if columns else 0)
                continue
            column = columns[0]
            minimum = Decimal(rule.minimum) if rule.minimum is not None else None
            self.bound.append((column, rule, minimum))
            if rule.unique:
                self.seen[column] = {}

    def validate(self, row, start, end):
        for column, rule, minimum in self.bound:
            value = row[column - 1].strip() if self.trim_whitespace else row[column - 1]
            if not value:
                if rule.required:
                    yield Issue('REQUIRED', 'A value is required.', start, end, column)
                continue
            key = value
            if rule.kind != 'text':
                try:
                    key = parse_number(value, rule.kind, self.decimal_separator)
                except ValueError:
                    yield Issue('INVALID_NUMBER',
                                'Expected an integer (optional sign and digits only).' if rule.kind == 'integer' else
                                f'Expected a finite decimal using "{self.decimal_separator}" (maximum 1,024 characters; no exponent or thousands separators).',
                                start, end, column)
                    continue
                if minimum is not None and key < minimum:
                    yield Issue('BELOW_MINIMUM', f'Value must be greater than or equal to {rule.minimum}.', start, end, column)
            if rule.unique:
                seen = self.seen[column]
                if key in seen:
                    first = seen[key]
                    yield Issue('DUPLICATE_VALUE', f'Value duplicates the record starting at line {first}.',
                                start, end, column, first)
                    continue
                # Account conservatively for key, line and dictionary overhead.
                cost = sys.getsizeof(key) + sys.getsizeof(start) + 128
                if self.key_count >= MAX_UNIQUE_KEYS or self.key_bytes + cost > MAX_UNIQUE_BYTES:
                    self.incomplete = True
                    self.exhausted = True
                    yield Issue('RULE_RESOURCE_LIMIT', 'Uniqueness resource limit reached; analysis stopped.', start, end, column)
                    return
                seen[key] = start
                self.key_count += 1
                self.key_bytes += cost
