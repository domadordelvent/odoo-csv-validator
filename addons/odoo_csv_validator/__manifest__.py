{
    'name': 'Odoo CSV Validator',
    'version': '18.0.2.2.0',
    'summary': 'Validate CSV structure and configurable column rules before importing',
    'category': 'Productivity',
    'author': 'Odoo CSV Validator contributors',
    'license': 'LGPL-3',
    'depends': ['base'],
    'data': ['security/profile_groups.xml', 'security/ir.model.access.csv', 'security/validation_rules.xml', 'views/csv_validation_views.xml', 'views/validation_profile_views.xml'],
    'application': True,
    'installable': True,
}
