"""Exercise the authenticated JSON-RPC boundary, not just ORM calls."""
import base64
import json

from odoo.tests import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestResultRpc(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(cls.env, login='result_rpc_alice',
                                 password='rpc-test-password', groups='base.group_user')
        cls.bob = new_test_user(cls.env, login='result_rpc_bob',
                               password='rpc-test-password', groups='base.group_user')
        cls.wizard = cls.env['csv.validation.wizard'].with_user(cls.alice).create({
            'file_data': base64.b64encode(b'a,b\n1'), 'filename': 'rpc.csv'})
        cls.wizard.action_validate()

    def rpc(self, model, method, args, context=None):
        payload = {'jsonrpc': '2.0', 'method': 'call', 'id': 1,
                   'params': {'model': model, 'method': method, 'args': args,
                              'kwargs': {'context': context or {}}}}
        response = self.url_open('/web/dataset/call_kw/' + model + '/' + method,
                                 data=json.dumps(payload),
                                 headers={'Content-Type': 'application/json'})
        return response.json()

    def assert_denied(self, response):
        self.assertIn('error', response, response)
        self.assertEqual(response['error']['data']['name'], 'odoo.exceptions.AccessError')

    def test_rpc_cannot_write_result_fields(self):
        self.authenticate('result_rpc_alice', 'rpc-test-password')
        for values in ({'state': 'valid'}, {'record_count': 9}, {'blank_line_count': 9},
                       {'issue_count': 0}, {'details_truncated': False},
                       {'report_data': 'Zm9yZ2Vk'}, {'report_filename': 'forged.csv'},
                       {'header_snapshot': ['forged']}, {'profile_snapshot': {'name': 'forged'}},
                       {'applied_profile_name': 'forged'}, {'issue_ids': [[5, 0, 0]]}):
            with self.subTest(values=values):
                self.assert_denied(self.rpc('csv.validation.wizard', 'write', [[self.wizard.id], values],
                                           {'internal_validation': True, 'sudo': True}))
        result = self.rpc('csv.validation.wizard', 'read', [[self.wizard.id], ['state', 'issue_count']])
        self.assertEqual(result['result'][0]['state'], 'errors')
        self.assertEqual(result['result'][0]['issue_count'], 1)

    def test_rpc_create_and_context_defaults_cannot_forge_results(self):
        self.authenticate('result_rpc_alice', 'rpc-test-password')
        self.assert_denied(self.rpc('csv.validation.wizard', 'create', [{
            'file_data': 'YQ==', 'state': 'valid'}]))
        result = self.rpc('csv.validation.wizard', 'create', [{'file_data': 'YQ=='}],
                          {'default_state': 'valid', 'default_record_count': 9,
                           'default_report_data': 'Zm9yZ2Vk', 'default_header_snapshot': ['forged'],
                           'default_issue_ids': [[0, 0, {'code': 'FORGED'}]]})
        self.assertNotIn('error', result)
        values = self.rpc('csv.validation.wizard', 'read', [[result['result']],
                          ['state', 'record_count', 'report_data', 'header_snapshot', 'issue_ids']])['result'][0]
        self.assertEqual(values['state'], 'draft')
        self.assertEqual(values['record_count'], 0)
        self.assertFalse(values['report_data'])
        self.assertFalse(values['header_snapshot'])
        self.assertFalse(values['issue_ids'])

    def test_rpc_issue_creation_modification_and_deletion_denied(self):
        self.authenticate('result_rpc_alice', 'rpc-test-password')
        self.assert_denied(self.rpc('csv.validation.issue', 'create', [{
            'wizard_id': self.wizard.id, 'code': 'FORGED'}]))
        for method, args in [('write', [self.wizard.issue_ids.ids, {'message': 'forged'}]),
                             ('write', [self.wizard.issue_ids.ids, {'wizard_id': self.wizard.id}]),
                             ('unlink', [self.wizard.issue_ids.ids])]:
            self.assert_denied(self.rpc('csv.validation.issue', method, args,
                                       {'internal_validation': True}))

    def test_rpc_private_persistence_methods_are_inaccessible(self):
        self.authenticate('result_rpc_alice', 'rpc-test-password')
        for model, method, args in [
                ('csv.validation.wizard', '_store_validation_result', [[self.wizard.id], {'state': 'valid'}, []]),
                ('csv.validation.issue', '_create_validation_issues', [self.wizard.id, []]),
                ('csv.validation.issue', '_unlink_validation_issues', [self.wizard.issue_ids.ids])]:
            response = self.rpc(model, method, args)
            self.assertIn('error', response)
            self.assertIn('private', response['error']['data']['message'].lower())

    def test_rpc_validation_and_invalidation_remain_available(self):
        self.authenticate('result_rpc_alice', 'rpc-test-password')
        result = self.rpc('csv.validation.wizard', 'action_validate', [[self.wizard.id]])
        self.assertNotIn('error', result)
        result = self.rpc('csv.validation.wizard', 'write', [[self.wizard.id], {'delimiter': ';'}])
        self.assertNotIn('error', result)
        values = self.rpc('csv.validation.wizard', 'read', [[self.wizard.id],
                          ['state', 'report_data', 'issue_ids']])['result'][0]
        self.assertEqual(values['state'], 'draft')
        self.assertFalse(values['report_data'])
        self.assertFalse(values['issue_ids'])

    def test_rpc_cannot_validate_or_read_another_users_results(self):
        self.authenticate('result_rpc_bob', 'rpc-test-password')
        self.assert_denied(self.rpc('csv.validation.wizard', 'action_validate', [[self.wizard.id]]))
        self.assert_denied(self.rpc('csv.validation.wizard', 'read', [[self.wizard.id], ['report_data']]))


    def test_rpc_nul_csv_returns_controlled_result(self):
        self.authenticate('result_rpc_alice', 'rpc-test-password')
        data = b'a\x00b\n1'
        created = self.rpc('csv.validation.wizard', 'create', [{
            'file_data': base64.b64encode(data).decode()}])
        self.assertNotIn('error', created)
        wizard_id = created['result']
        self.assertNotIn('error', self.rpc('csv.validation.wizard', 'action_validate', [[wizard_id]]))
        values = self.rpc('csv.validation.wizard', 'read', [[wizard_id],
                          ['state', 'header_snapshot', 'issue_ids', 'report_data']])['result'][0]
        self.assertEqual(values['state'], 'incomplete')
        self.assertFalse(values['header_snapshot'])
        self.assertNotIn(b'\x00', base64.b64decode(values['report_data']))
        issue = self.rpc('csv.validation.issue', 'read', [values['issue_ids'], ['code', 'line']])['result'][0]
        self.assertEqual((issue['code'], issue['line']), ('NUL_CHARACTER', 1))
