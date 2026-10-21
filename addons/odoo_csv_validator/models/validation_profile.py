from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError

from ..services.column_rules import ColumnRule, check_rules
from ..services.issue_presentation import configuration_message


class ValidationProfile(models.Model):
    _name = 'csv.validation.profile'
    _description = 'CSV validation profile'
    _order = 'name, id'

    name = fields.Char(required=True)
    owner_id = fields.Many2one('res.users', required=True, default=lambda self: self.env.user,
                               readonly=True, index=True, ondelete='cascade')
    trim_whitespace = fields.Boolean(default=True, string='Trim spaces for comparison')
    decimal_separator = fields.Selection([('.', 'Point (.)'), (',', 'Comma (,)')], default='.', required=True)
    rule_ids = fields.One2many('csv.validation.profile.rule', 'profile_id', string='Column rules', copy=True)

    @api.constrains('owner_id', 'name')
    def _check_owner(self):
        for profile in self:
            if not profile.name.strip():
                raise ValidationError(_('A profile name cannot be blank.'))
            if not self.env.su and profile.owner_id != self.env.user:
                raise AccessError(_('Profiles must belong to the current user.'))

    def write(self, vals):
        if 'owner_id' in vals and not self.env.su:
            raise AccessError(_('Profile ownership cannot be changed.'))
        return super().write(vals)

    def _rule_specs(self):
        self.ensure_one()
        self.check_access('read')
        self.rule_ids.check_access('read')
        return tuple(ColumnRule(rule.column_name, rule.required, rule.unique,
                                rule.data_type, rule.minimum if rule.use_minimum else None)
                     for rule in self.rule_ids.sorted('id'))

    def _check_rule_configuration(self):
        for profile in self:
            try:
                check_rules(profile._rule_specs())
            except ValueError as exc:
                raise ValidationError(configuration_message(self.env, str(exc))) from exc


class ValidationProfileRule(models.Model):
    _name = 'csv.validation.profile.rule'
    _description = 'CSV column validation rule'
    _rec_name = 'column_name'
    _order = 'id'
    _sql_constraints = [
        ('profile_column_unique', 'unique(profile_id, column_name)',
         'Each column can only be configured once per profile.'),
    ]

    profile_id = fields.Many2one('csv.validation.profile', required=True, ondelete='cascade', index=True)
    column_name = fields.Char(required=True, string='Column header')
    required = fields.Boolean(string='Required value')
    unique = fields.Boolean(string='Unique values')
    data_type = fields.Selection([('text', 'Text'), ('integer', 'Integer'), ('decimal', 'Decimal')], default='text', required=True)
    use_minimum = fields.Boolean(string='Check minimum')
    minimum = fields.Char(string='Minimum (decimal point)', help='Inclusive exact decimal threshold, using a point; e.g. -2.50.')

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([dict(vals, column_name=vals['column_name'].strip())
                               if isinstance(vals.get('column_name'), str) else vals
                               for vals in vals_list])

    def write(self, vals):
        if isinstance(vals.get('column_name'), str):
            vals = dict(vals, column_name=vals['column_name'].strip())
        return super().write(vals)

    @api.constrains('profile_id', 'column_name', 'required', 'unique', 'data_type', 'use_minimum', 'minimum')
    def _check_configuration(self):
        for rule in self:
            rule.profile_id.check_access('write')
            if rule.use_minimum and not rule.minimum:
                raise ValidationError(_('Enter a minimum value.'))
        self.mapped('profile_id')._check_rule_configuration()
