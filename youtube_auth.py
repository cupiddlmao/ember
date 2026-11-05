"""Google device authorization; credentials and refresh tokens stay local."""
from functools import partial
import time
from urllib.parse import urlsplit

import requests
from ytmusicapi import OAuthCredentials, YTMusic

from services import DATA, load_private, save_private

OAUTH_FILE = DATA / 'youtube-oauth.json'
CLIENT_FILE = DATA / 'youtube-oauth-client.json'
BROWSER_FILE = DATA / 'youtube-transfer-auth.json'

ERRORS = {
    'access_denied': 'Google denied access. Add your signing-in account under Audience → Test users, then start again.',
    'invalid_client': 'Google rejected the client ID or secret. Use the matching TV/limited-input client credentials.',
    'unauthorized_client': 'Google does not allow this OAuth client to use device sign-in. Check its client type.',
    'expired_token': 'The sign-in code expired. Start again for a new code.',
    'invalid_grant': 'Google rejected this authorization code. Start a fresh sign-in.',
}


class SignInError(RuntimeError):
    """Only fixed, credential-free messages may reach the UI."""


def credentials(client_id, client_secret):
    if not client_id.endswith('.apps.googleusercontent.com') or not client_secret.strip():
        raise ValueError('Enter your Google OAuth client ID and client secret.')
    session = requests.Session()
    session.request = partial(session.request, timeout=15)
    return OAuthCredentials(client_id.strip(), client_secret.strip(), session=session)


def begin(client_id, client_secret):
    try:
        provider = credentials(client_id, client_secret)
        code = provider.get_code()
        if code.get('error'):
            raise SignInError(ERRORS.get(code['error'], 'Google refused to issue a sign-in code. Check the project configuration.'))
        url = urlsplit(code.get('verification_url', ''))
        if url.scheme != 'https' or url.netloc not in ('www.google.com', 'google.com', 'accounts.google.com'):
            raise ValueError('Invalid authorization URL')
        if not all(code.get(k) for k in ('device_code', 'user_code', 'expires_in')):
            raise ValueError('Missing device authorization fields')
        code['deadline'] = time.monotonic() + min(float(code['expires_in']), 900)
        return provider, code
    except SignInError:
        raise
    except Exception:
        raise RuntimeError('Google could not start sign-in. Check your TV/limited-input OAuth client and enabled YouTube Data API.') from None


def finish(provider, code, cancelled):
    interval = max(5, float(code.get('interval', 5)))
    while time.monotonic() < code['deadline']:
        if cancelled.wait(interval):
            return False
        try:
            token = provider.token_from_code(code['device_code'])
            error = token.get('error')
            if error == 'authorization_pending':
                continue
            if error == 'slow_down':
                interval += 5
                continue
            if error:
                raise SignInError(ERRORS.get(error, 'Google refused authorization. Start a fresh sign-in and check project access.'))
            if not all(token.get(k) for k in ('access_token', 'refresh_token', 'expires_in', 'scope', 'token_type')):
                raise SignInError('Google returned an incomplete token. Start a new sign-in.')
            token = {k: token[k] for k in ('access_token', 'refresh_token', 'expires_in', 'scope', 'token_type')}
            token['expires_at'] = int(time.time()) + int(token['expires_in'])
            # Account-menu parsing is separate from successful OAuth approval.
            YTMusic(token, oauth_credentials=provider)
            if cancelled.is_set():
                return False
            try:
                save_private(CLIENT_FILE, {'client_id': provider.client_id, 'client_secret': provider.client_secret})
                save_private(OAUTH_FILE, token)
            except OSError:
                raise SignInError('Could not save Google login. Check local configuration-directory permissions.') from None
            return True
        except SignInError:
            raise
        except Exception:
            raise SignInError('Google token exchange failed. Check your connection and matching client secret, then retry.') from None
    raise RuntimeError('Google sign-in expired. Start again for a new code.')


def auth_path():
    return OAUTH_FILE if OAUTH_FILE.exists() else BROWSER_FILE


def client(auth):
    if str(auth) != str(OAUTH_FILE):
        return YTMusic(str(auth))
    config = load_private(CLIENT_FILE)
    return YTMusic(str(OAUTH_FILE), oauth_credentials=credentials(config.get('client_id', ''), config.get('client_secret', '')))
