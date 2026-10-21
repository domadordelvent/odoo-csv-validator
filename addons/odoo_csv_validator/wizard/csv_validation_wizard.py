import base64
import logging
import binascii
import json
import traceback
from dataclasses import asdict

from psycopg2 import errors as pg_errors

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services.csv_validator import Issue, decode_upload, validate_csv, MAX_ISSUES, MAX_COLUMNS
from ..services.issue_presentation import describe_issue, export_presented_report, read_headers, column_details, upload_message

_logger = logging.getLogger(__name__)

# These are server-side persistence budgets, including privileged maintenance.
MAX_REPORT_BYTES = 10 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 512 * 1024
MAX_RESULT_NAME_BYTES = 4096
MAX_ISSUE_MESSAGE_BYTES = 64 * 1024
MAX_ISSUE_CODE_BYTES = 256
INPUT_FIELDS = frozenset({'filename', 'file_data', 'encoding', 'delimiter', 'profile_id'})


def _reject_nul(value):
    """Reject text/JSON NUL before the ORM can send it to PostgreSQL."""
    if isinstance(value, (str, bytes)):
        if ('\x00' if isinstance(value, str) else b'\x00') in value:
            raise ValidationError(_('Validation results cannot contain NUL characters.'))
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_nul(key)
            _reject_nul(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_nul(item)


def _check_text_size(value, limit):
    if value and (len(value) > limit or len(value.encode('utf-8')) > limit):
        raise ValidationError(_('Validation result exceeds the allowed storage limits.'))


def _check_counter(value, maximum=2**31 - 1):
    if not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValidationError(_('Validation result exceeds the allowed storage limits.'))



class CsvValidationWizard(models.TransientModel):
    _name = 'csv.validation.wizard'
    _description = 'CSV structural validation'
    _transient_max_hours = 1

    filename = fields.Char()
    file_data = fields.Binary(string='CSV file', attachment=False, required=True)
    encoding = fields.Selection([('utf-8-sig', 'UTF-8 (with or without BOM)'), ('cp1252', 'Windows-1252')], default='utf-8-sig', required=True)
    delimiter = fields.Selection([(',', 'Comma (,)'), (';', 'Semicolon (;)'), ('\t', 'Tab')], default=',', required=True)
    state = fields.Selection([('draft', 'Ready'), ('valid', 'Valid structure'), ('errors', 'Issues found'), ('incomplete', 'Incomplete analysis')], default='draft', readonly=True)
    record_count = fields.Integer(readonly=True)
    blank_line_count = fields.Integer(readonly=True)
    issue_count = fields.Integer(readonly=True)
    details_truncated = fields.Boolean(readonly=True)
    report_data = fields.Binary(attachment=False, readonly=True)
    report_filename = fields.Char(readonly=True)
    issue_ids = fields.One2many('csv.validation.issue', 'wizard_id', readonly=True)
    profile_id = fields.Many2one('csv.validation.profile', string='Validation Profile', ondelete='set null')
    applied_profile_name = fields.Char(string='Profile used for this result', readonly=True)
    profile_snapshot = fields.Json(readonly=True)
    header_snapshot = fields.Json(readonly=True)
    presentation_headers = fields.Json(compute='_compute_presentation_headers')

    @api.depends('header_snapshot', 'file_data', 'encoding', 'delimiter')
    def _compute_presentation_headers(self):
        for wizard in self:
            if isinstance(wizard.header_snapshot, list):
                wizard.presentation_headers = wizard.header_snapshot
            elif wizard.file_data:
                # Old transient results have no snapshot; resolve once per wizard.
                try:
                    data = decode_upload(wizard.file_data)
                    wizard.presentation_headers = read_headers(data, wizard.encoding, wizard.delimiter)
                except ValueError:
                    wizard.presentation_headers = []
            else:
                wizard.presentation_headers = []

    @api.constrains('profile_id')
    def _check_profile_access(self):
        for wizard in self:
            if wizard.profile_id:
                wizard.profile_id.check_access('read')

    @api.depends('filename')
    @api.depends_context('lang')
    def _compute_display_name(self):
        for wizard in self:
            wizard.display_name = (_('CSV validation: %s', wizard.filename)
                                   if wizard.filename else _('CSV validation'))

    @api.constrains('file_data')
    def _check_upload(self):
        for record in self:
            if record.file_data:
                try:
                    decode_upload(record.file_data)
                except ValueError as exc:
                    raise ValidationError(upload_message(self.env, str(exc))) from exc

    @api.model_create_multi
    def create(self, vals_list):
        _reject_nul(vals_list)
        if not self.env.su:
            if any(set(vals) - INPUT_FIELDS for vals in vals_list):
                raise AccessError(_('Validation results can only be changed by the validation process.'))
            # default_get is called inside super().create: strip injected defaults
            # before it runs, including relational commands and computed fields.
            context = {key: value for key, value in self.env.context.items()
                       if not key.startswith('default_') or key[8:] in INPUT_FIELDS}
            return super(CsvValidationWizard, self.with_context(context)).create(vals_list)
        return super().create(vals_list)

    def write(self, vals):
        _reject_nul(vals)
        if not self.env.su and set(vals) - INPUT_FIELDS:
            raise AccessError(_('Validation results can only be changed by the validation process.'))
        if {'file_data', 'encoding', 'delimiter', 'profile_id'} & vals.keys():
            self.check_access('write')
            self.issue_ids._unlink_validation_issues()
            vals = dict(vals, state='draft', record_count=0, blank_line_count=0,
                        issue_count=0, details_truncated=False, report_data=False,
                        report_filename=False, applied_profile_name=False,
                        profile_snapshot=False, header_snapshot=False)
        return super().write(vals)

    @api.constrains('report_data', 'report_filename', 'applied_profile_name',
                    'profile_snapshot', 'header_snapshot', 'issue_ids',
                    'record_count', 'blank_line_count', 'issue_count')
    def _check_result_storage(self):
        for wizard in self.with_context(bin_size=False):
            encoded = wizard.report_data
            if encoded:
                if len(encoded) > 4 * ((MAX_REPORT_BYTES + 2) // 3):
                    raise ValidationError(_('Validation result exceeds the allowed storage limits.'))
                try:
                    report = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError, TypeError) as exc:
                    raise ValidationError(_('Validation result exceeds the allowed storage limits.')) from exc
                if len(report) > MAX_REPORT_BYTES:
                    raise ValidationError(_('Validation result exceeds the allowed storage limits.'))
            _check_text_size(wizard.report_filename, 255)
            _check_text_size(wizard.applied_profile_name, MAX_RESULT_NAME_BYTES)
            for snapshot in (wizard.profile_snapshot, wizard.header_snapshot):
                if snapshot:
                    _check_text_size(json.dumps(snapshot, ensure_ascii=False), MAX_SNAPSHOT_BYTES)
            if len(wizard.issue_ids) > MAX_ISSUES:
                raise ValidationError(_('Validation result exceeds the allowed storage limits.'))
            for value in (wizard.record_count, wizard.blank_line_count, wizard.issue_count):
                _check_counter(value)

    def _store_validation_result(self, vals, issues):
        """Private RPC-inaccessible entry point; never elevate the caller."""
        self.ensure_one()
        self.check_access('write')
        _reject_nul((vals, issues))
        try:
            with self.env.cr.savepoint():
                self.issue_ids._unlink_validation_issues()
                super(CsvValidationWizard, self).write(vals)
                self.env['csv.validation.issue']._create_validation_issues(self, issues)
                self._check_result_storage()
        except (AccessError, UserError, pg_errors.SerializationFailure,
                pg_errors.DeadlockDetected, pg_errors.LockNotAvailable):
            # Preserve expected errors and Odoo's automatic concurrency retries.
            raise
        except Exception as exc:
            # Catch only after the savepoint has restored the cursor and cache.
            # Never log exception text, SQL parameters or uploaded content.
            _logger.error('Unexpected CSV result persistence failure (%s)\n%s',
                          type(exc).__name__, ''.join(traceback.format_tb(exc.__traceback__)))
            raise UserError(_('Validation results could not be saved. Contact your administrator.')) from None

    def _validation_rules(self):
        """Extension point: trusted addons can return pure Python rule callables."""
        return ()

    def action_validate(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('base.group_user'):
            raise AccessError(_('Only internal users can validate CSV files.'))
        try:
            data = decode_upload(self.file_data)
        except ValueError as exc:
            raise UserError(upload_message(self.env, str(exc))) from exc
        options = {}
        snapshot = False
        profile_name = False
        if self.profile_id:
            # Permission errors must propagate; never downgrade to structural validation.
            self.profile_id.check_access('read')
            specs = self.profile_id._rule_specs()
            options = {'column_rules': specs, 'trim_whitespace': self.profile_id.trim_whitespace,
                       'decimal_separator': self.profile_id.decimal_separator}
            profile_name = self.profile_id.name
            snapshot = {'name': profile_name, 'rules': [asdict(spec) for spec in specs],
                        'trim_whitespace': options['trim_whitespace'],
                        'decimal_separator': options['decimal_separator']}
        try:
            result = validate_csv(data, self.encoding, self.delimiter, rules=self._validation_rules(), **options)
            headers = read_headers(data, self.encoding, self.delimiter)
            presented = [describe_issue(self.env, issue, headers, snapshot) for issue in result.issues]
            report = export_presented_report(result, presented)
        except Exception as exc:
            # Traceback only: never log the file, filename, values or exception text.
            _logger.error('Unexpected CSV validation failure (%s)\n%s',
                          type(exc).__name__, ''.join(traceback.format_tb(exc.__traceback__)))
            raise UserError(_('Validation could not finish. Contact your administrator.')) from None
        self._store_validation_result({
            'state': 'incomplete' if not result.complete else 'errors' if result.issue_count else 'valid',
            'record_count': result.records,
            'blank_line_count': result.blank_lines,
            'issue_count': result.issue_count,
            'details_truncated': result.issue_count > len(result.issues),
            'report_data': base64.b64encode(report),
            'report_filename': 'csv-validation-report.csv',
            'applied_profile_name': profile_name,
            'profile_snapshot': snapshot,
            'header_snapshot': headers,
        }, [{
            'code': issue.code, 'message': issue.message, 'line': issue.line,
            'end_line': issue.end_line, 'column': issue.column, 'first_line': issue.first_line,
        } for issue in result.issues])
        return {'type': 'ir.actions.act_window', 'name': _('CSV Validator'), 'res_model': self._name,
                'res_id': self.id, 'view_mode': 'form', 'target': 'current'}


class CsvValidationIssue(models.TransientModel):
    _name = 'csv.validation.issue'
    _description = 'CSV validation issue'
    _transient_max_hours = 1
    _order = 'id'

    wizard_id = fields.Many2one('csv.validation.wizard', required=True, ondelete='cascade')
    code = fields.Char(readonly=True)
    message = fields.Text(string='Technical details', readonly=True)
    line = fields.Integer(string='Start line', readonly=True)
    end_line = fields.Integer(readonly=True)
    column = fields.Integer(string='Column number', readonly=True)
    first_line = fields.Integer(string='First occurrence line', readonly=True)
    column_name = fields.Char(compute='_compute_column_details', string='Column name')
    column_label = fields.Char(compute='_compute_column_details', string='Column')
    issue_type = fields.Selection([('structure', 'Structure'), ('content', 'Content'), ('other', 'Other')],
                                  compute='_compute_presentation', string='Issue type')
    user_message = fields.Text(compute='_compute_presentation', string='Message')
    restriction_details = fields.Text(compute='_compute_presentation', string='Restrictions')

    @api.model_create_multi
    def create(self, vals_list):
        _reject_nul(vals_list)
        if not self.env.su:
            raise AccessError(_('Validation results can only be changed by the validation process.'))
        return super().create(vals_list)

    def write(self, vals):
        _reject_nul(vals)
        if not self.env.su:
            raise AccessError(_('Validation results can only be changed by the validation process.'))
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise AccessError(_('Validation results can only be changed by the validation process.'))
        return super().unlink()

    def _unlink_validation_issues(self):
        # super() still enforces the original ACL and ownership rules.
        return super(CsvValidationIssue, self).unlink()

    @api.model
    def _create_validation_issues(self, wizard, values):
        wizard.ensure_one()
        wizard.check_access('write')
        _reject_nul(values)
        if len(values) > MAX_ISSUES:
            raise ValidationError(_('Validation result exceeds the allowed storage limits.'))
        context = {key: value for key, value in self.env.context.items()
                   if not key.startswith('default_')}
        records = super(CsvValidationIssue, self.with_context(context)).create(
            [dict(vals, wizard_id=wizard.id) for vals in values])
        wizard._check_result_storage()
        return records

    @api.constrains('wizard_id', 'code', 'message', 'line', 'end_line', 'column', 'first_line')
    def _check_issue_storage(self):
        for issue in self:
            _check_text_size(issue.code, MAX_ISSUE_CODE_BYTES)
            _check_text_size(issue.message, MAX_ISSUE_MESSAGE_BYTES)
            _check_counter(issue.column, MAX_COLUMNS)
            for value in (issue.line, issue.end_line, issue.first_line):
                _check_counter(value)
        self.mapped('wizard_id')._check_result_storage()

    @api.depends('column', 'wizard_id.presentation_headers')
    @api.depends_context('lang')
    def _compute_column_details(self):
        for issue in self:
            issue.column_name, issue.column_label = column_details(
                self.env, issue.column, issue.wizard_id.presentation_headers or [])

    @api.depends('code', 'message', 'line', 'end_line', 'column', 'first_line',
                 'wizard_id.presentation_headers', 'wizard_id.profile_snapshot')
    @api.depends_context('lang')
    def _compute_presentation(self):
        for issue in self:
            diagnostic = Issue(issue.code or '', issue.message or '', issue.line,
                               issue.end_line, issue.column, issue.first_line)
            display = describe_issue(self.env, diagnostic, issue.wizard_id.presentation_headers or [],
                                     issue.wizard_id.profile_snapshot)
            issue.issue_type = display['issue_type']
            issue.user_message = display['user_message']
            issue.restriction_details = display['restriction_details']

    @api.constrains('wizard_id')
    def _check_wizard_owner(self):
        for issue in self:
            issue.wizard_id.check_access('write')
