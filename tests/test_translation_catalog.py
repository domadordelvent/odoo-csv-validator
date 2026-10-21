"""Catalog regression checks using only the Python standard library."""
import ast
from pathlib import Path
import re
import unittest

ADDON = Path(__file__).resolve().parents[1] / 'addons' / 'odoo_csv_validator'


def read_catalog(path):
    catalog = {}
    for block in re.split(r'\n\s*\n', path.read_text().strip()):
        values, current = {}, None
        for line in block.splitlines():
            if line.startswith(('msgid ', 'msgstr ')):
                current, value = line.split(' ', 1)
                values[current] = ast.literal_eval(value)
            elif line.startswith('"') and current:
                values[current] += ast.literal_eval(line)
        if 'msgid' in values:
            if values['msgid'] in catalog:
                raise AssertionError('Duplicate catalog message: ' + values['msgid'])
            catalog[values['msgid']] = values.get('msgstr', '')
    return catalog


class TestTranslationCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = read_catalog(ADDON / 'i18n' / 'odoo_csv_validator.pot')
        cls.catalan = read_catalog(ADDON / 'i18n' / 'ca.po')

    def test_catalan_covers_entire_catalog(self):
        self.assertEqual(set(self.catalan), set(self.template))
        self.assertTrue(all(self.catalan.values()))

    def test_formatting_parameters_are_preserved(self):
        for source, target in self.catalan.items():
            if not source:
                continue
            with self.subTest(source=source):
                self.assertEqual(sorted(re.findall(r'%\([^)]+\)[a-z]', source)),
                                 sorted(re.findall(r'%\([^)]+\)[a-z]', target)))
                self.assertEqual(source.count('%s'), target.count('%s'))

    def test_template_remains_ready_for_new_languages(self):
        self.assertTrue(all(not translated for source, translated in self.template.items() if source))

    def test_template_covers_python_translation_literals(self):
        for path in ADDON.rglob('*.py'):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, 'id', '')
                source = node.args[0]
                if name == '_' and isinstance(source, ast.Constant) and isinstance(source.value, str):
                    with self.subTest(file=path.name, source=source.value):
                        self.assertIn(source.value, self.template)
