"""HTTP checks against the running local Docker service; no credentials needed."""
import http.cookiejar
import json
import os
import unittest
import urllib.request


class TestLocalEnvironment(unittest.TestCase):
    def setUp(self):
        self.base_url = os.environ.get('CSV_HTTP_URL', 'http://localhost:8070')
        self.client = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def test_root_opens_login_without_database_parameter(self):
        response = self.client.open(self.base_url + '/', timeout=20)
        body = response.read().decode()
        self.assertEqual(response.status, 200)
        self.assertIn('/web/login', response.url)
        self.assertNotIn('db=', response.url)
        self.assertIn('name="login"', body)
        self.assertIn('name="password"', body)
        self.assertNotIn('/web/database/selector', response.url)

    def test_database_manager_is_disabled(self):
        body = self.client.open(self.base_url + '/web/database/manager', timeout=20).read().decode()
        self.assertIn('The database manager has been disabled by the administrator', body)

    def test_web_service_rejects_test_database(self):
        payload = json.dumps({
            'jsonrpc': '2.0', 'method': 'call', 'id': 1,
            'params': {'db': 'csv_validator_test', 'login': 'unused', 'password': 'unused'},
        }).encode()
        request = urllib.request.Request(self.base_url + '/web/session/authenticate', payload,
                                         {'Content-Type': 'application/json'})
        result = json.load(self.client.open(request, timeout=20))
        self.assertIn('error', result)
        self.assertEqual(result['error']['data']['message'], 'Database not found.')


if __name__ == '__main__':
    unittest.main()
