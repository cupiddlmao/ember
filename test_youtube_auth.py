import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import youtube_auth as auth


class GoogleAuthTests(unittest.TestCase):
    def test_begin_checks_google_url_and_bounds_deadline(self):
        provider = MagicMock()
        provider.get_code.return_value = {'verification_url': 'https://www.google.com/device', 'user_code': 'TEST', 'device_code': 'fake', 'expires_in': 9999}
        with patch('youtube_auth.credentials', return_value=provider):
            _, code = auth.begin('id', 'secret')
            self.assertLessEqual(code['deadline'], time.monotonic() + 900)
            provider.get_code.return_value['verification_url'] = 'https://evil.test/'
            with self.assertRaises(RuntimeError):
                auth.begin('id', 'secret')

    def test_pending_approval_saved_privately_and_cancel_does_not_save(self):
        provider = MagicMock(client_id='fake.apps.googleusercontent.com', client_secret='fake-secret')
        token = {'access_token': 'fake-access', 'refresh_token': 'fake-refresh', 'expires_in': 3600, 'scope': 'test', 'token_type': 'Bearer'}
        provider.token_from_code.side_effect = [{'error': 'authorization_pending'}, {'error': 'slow_down'}, token]
        cancel = MagicMock()
        cancel.wait.return_value = False
        cancel.is_set.return_value = False
        code = {'device_code': 'fake-device', 'deadline': time.monotonic() + 100}
        with tempfile.TemporaryDirectory() as folder, patch('youtube_auth.YTMusic') as api, patch.object(auth, 'CLIENT_FILE', Path(folder)/'client.json'), patch.object(auth, 'OAUTH_FILE', Path(folder)/'oauth.json'):
            api.return_value.get_account_info.return_value = {'accountName': 'Example'}
            api.return_value.get_account_info.side_effect = RuntimeError('account menu unsupported')
            self.assertTrue(auth.finish(provider, code, cancel))
            api.return_value.get_account_info.assert_not_called()
            self.assertEqual(cancel.wait.call_args.args[0], 10)
            self.assertEqual(json.loads(auth.OAUTH_FILE.read_text())['refresh_token'], 'fake-refresh')
            self.assertEqual(auth.OAUTH_FILE.stat().st_mode & 0o777, 0o600)
            self.assertEqual(auth.CLIENT_FILE.stat().st_mode & 0o777, 0o600)
            before = auth.OAUTH_FILE.read_bytes()
            cancel.wait.return_value = True
            self.assertFalse(auth.finish(provider, code, cancel))
            self.assertEqual(auth.OAUTH_FILE.read_bytes(), before)

    def test_decline_and_expiration_are_sanitized(self):
        provider, cancel = MagicMock(), MagicMock()
        cancel.wait.return_value = False
        provider.token_from_code.return_value = {'error': 'access_denied', 'private': 'do-not-print'}
        with self.assertRaises(RuntimeError) as error:
            auth.finish(provider, {'device_code': 'fake', 'deadline': time.monotonic()+60}, cancel)
        self.assertNotIn('do-not-print', str(error.exception))
        self.assertIn('Test users', str(error.exception))
        with self.assertRaisesRegex(RuntimeError, 'expired'):
            auth.finish(provider, {'deadline': 0}, cancel)

    def test_oauth_client_gets_refresh_credentials_browser_client_does_not(self):
        with patch('youtube_auth.YTMusic') as api, patch('youtube_auth.load_private', return_value={'client_id': 'fake.apps.googleusercontent.com', 'client_secret': 'fake'}), patch('youtube_auth.credentials') as creds:
            auth.client(auth.OAUTH_FILE)
            api.assert_called_with(str(auth.OAUTH_FILE), oauth_credentials=creds.return_value)
            auth.client(auth.BROWSER_FILE)
            api.assert_called_with(str(auth.BROWSER_FILE))
