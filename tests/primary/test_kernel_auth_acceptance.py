"""实际 Authlib 与 JoseRFC 的账户授权、持久化和刷新消费者。"""
import asyncio
import json
import time
import http.server
import urllib.request
import webbrowser
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
import pytest
from authlib.integrations.base_client import OAuthError
from authlib.oauth2.rfc7636.challenge import create_s256_code_challenge
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import RSAKey

from society0.kernel import auth as module


@pytest.fixture
def authorization_transport(monkeypatch):
    key = RSAKey.generate_key(parameters={'kid': 'offline-test-key'})
    state = SimpleNamespace(forms=[], nonce=None, subject='offline-subject', audience='issued-test-client',
                            scope=module.SCOPES, error=None, refresh_count=0,
                            entered=asyncio.Event(), release=asyncio.Event(), hold_refresh=False)

    async def handle(request):
        if str(request.url) == module.ISSUER + '/.well-known/openid-configuration':
            return httpx2.Response(200, json={'issuer': module.ISSUER, 'jwks_uri': module.ISSUER + '/test-jwks'})
        if str(request.url) == module.ISSUER + '/test-jwks':
            return httpx2.Response(200, json={'keys': [key.as_dict()]})
        assert str(request.url) == module.TOKEN
        form = parse_qs(request.content.decode())
        state.forms.append(form)
        if state.error:
            return httpx2.Response(400, json={'error': state.error})
        if form['grant_type'] == ['refresh_token']:
            state.refresh_count += 1
            state.entered.set()
            if state.hold_refresh:
                await asyncio.wait_for(state.release.wait(), 10)
        claims = {'iss': module.ISSUER, 'aud': state.audience, 'sub': state.subject,
                  'exp': int(time.time()) + 3600, 'nonce': state.nonce, 'email': 'offline@example.invalid'}
        signed = jwt.encode({'alg': 'RS256', 'kid': 'offline-test-key'}, claims, key)
        number = state.refresh_count
        return httpx2.Response(200, json={'access_token': f'offline-access-{number}',
            'refresh_token': f'offline-refresh-{number}', 'token_type': 'Bearer', 'expires_in': 3600,
            'scope': state.scope, 'id_token': signed})

    transport = httpx2.MockTransport(handle)
    oauth_client, http_client = module.AsyncOAuth2Client, httpx2.AsyncClient
    monkeypatch.setattr(module, 'AsyncOAuth2Client', lambda *args, **kwargs:
                        oauth_client(*args, transport=transport, **kwargs))
    monkeypatch.setattr(module, 'httpx2', SimpleNamespace(AsyncClient=lambda *args, **kwargs:
                        http_client(*args, transport=transport, **kwargs)))
    return state


def callback(attempt, **changes):
    values = {'state': attempt.state, 'code': 'offline-code', 'client_id': 'issued-test-client'}
    values.update(changes)
    return attempt.redirect_uri + '?' + urlencode(values)


async def authorize(directory, transport):
    auth = module.ChatGPTAuth('test-account', directory=directory)
    attempt = await auth.begin('http://127.0.0.1:1455/auth/callback')
    transport.nonce = attempt.nonce
    identity = await auth.callback(attempt, callback(attempt))
    return auth, attempt, identity


@pytest.mark.asyncio
async def test_dynamic_registration_pkce_oidc_and_reauthorization(tmp_path, authorization_transport):
    auth, attempt, identity = await authorize(tmp_path, authorization_transport)
    query = parse_qs(urlsplit(attempt.url).query)
    assert query['client_id'] == ['dynamic_agent_client']
    assert query['code_challenge_method'] == ['S256']
    assert query['code_challenge'] == [create_s256_code_challenge(attempt.verifier)]
    assert query['state'] == [attempt.state] and query['nonce'] == [attempt.nonce]
    assert query['resource'] == [module.RESOURCE] and query['ext_agent_host_id'] == [attempt.host_id]
    assert identity == {'subject': 'offline-subject', 'email': 'offline@example.invalid',
                        'client_id': 'issued-test-client'}
    form = authorization_transport.forms[0]
    assert form['client_id'] == ['issued-test-client'] and form['code_verifier'] == [attempt.verifier]
    assert form['redirect_uri'] == [attempt.redirect_uri] and form['resource'] == [module.RESOURCE]
    assert await module.ChatGPTAuth('test-account', directory=tmp_path).access_token() == 'offline-access-0'
    saved = json.loads(auth.path.read_text())
    repeated = await auth.begin(attempt.redirect_uri)
    repeated_query = parse_qs(urlsplit(repeated.url).query)
    assert repeated.client_id == 'issued-test-client' and repeated.host_id == attempt.host_id
    assert repeated_query['id_token_hint'] == [saved['token']['id_token']]
    assert repeated_query['login_hint'] == ['offline@example.invalid']
    authorization_transport.nonce = repeated.nonce
    assert await auth.callback(repeated, callback(repeated)) == identity


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['state', 'registration', 'nonce', 'audience', 'permission', 'oauth'])
async def test_authorization_error_never_persists_credentials(tmp_path, authorization_transport, failure):
    auth = module.ChatGPTAuth('test-account', directory=tmp_path)
    attempt = await auth.begin('http://127.0.0.1:1455/auth/callback')
    authorization_transport.nonce = attempt.nonce
    changes = {}
    expected = ValueError
    if failure == 'state': changes['state'] = 'other-state'
    elif failure == 'registration': changes['client_id'] = 'dynamic_agent_client'
    elif failure == 'nonce':
        authorization_transport.nonce = 'other-nonce'
        expected = JoseError
    elif failure == 'audience':
        authorization_transport.audience = 'other-client'
        expected = JoseError
    elif failure == 'permission': authorization_transport.scope = 'openid profile'
    else:
        authorization_transport.error = 'invalid_grant'
        expected = OAuthError
    with pytest.raises(expected):
        await auth.callback(attempt, callback(attempt, **changes))
    assert not auth.path.exists()
    if failure in ('state', 'registration'): assert authorization_transport.forms == []


@pytest.mark.asyncio
async def test_expired_refresh_serializes_accounts_and_rotates_tokens(tmp_path, authorization_transport):
    auth, _, _ = await authorize(tmp_path, authorization_transport)
    saved = json.loads(auth.path.read_text())
    saved['token']['expires_at'] = 0
    auth.path.write_text(json.dumps(saved))
    authorization_transport.hold_refresh = True
    second = module.ChatGPTAuth('test-account', directory=tmp_path)
    first_task = asyncio.create_task(auth.access_token())
    second_task = None
    try:
        await asyncio.wait_for(authorization_transport.entered.wait(), 10)
        second_task = asyncio.create_task(second.access_token())
        await asyncio.sleep(0)
        assert authorization_transport.refresh_count == 1 and not second_task.done()
        authorization_transport.release.set()
        assert await asyncio.wait_for(asyncio.gather(first_task, second_task), 10) == ['offline-access-1'] * 2
        assert authorization_transport.refresh_count == 1
        assert await second.access_token(force_refresh=True) == 'offline-access-2'
        refreshes = [form for form in authorization_transport.forms if form['grant_type'] == ['refresh_token']]
        assert [form['refresh_token'] for form in refreshes] == [['offline-refresh-0'], ['offline-refresh-1']]
        assert all(form['client_id'] == ['issued-test-client'] and form['resource'] == [module.RESOURCE]
                   for form in refreshes)
        assert json.loads(auth.path.read_text())['token']['refresh_token'] == 'offline-refresh-2'
    finally:
        authorization_transport.release.set()
        await asyncio.gather(*(task for task in (first_task, second_task) if task is not None), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['oauth', 'account'])
async def test_failed_refresh_keeps_previous_registration(tmp_path, authorization_transport, failure):
    auth, _, _ = await authorize(tmp_path, authorization_transport)
    original = auth.path.read_bytes()
    if failure == 'oauth': authorization_transport.error = 'invalid_grant'
    else: authorization_transport.subject = 'other-subject'
    with pytest.raises(OAuthError if failure == 'oauth' else ValueError):
        await auth.access_token(force_refresh=True)
    assert auth.path.read_bytes() == original


@pytest.mark.asyncio
@pytest.mark.parametrize('granted', [True, False])
async def test_login_real_loopback_callback_closes_listener(tmp_path, authorization_transport, monkeypatch, granted):
    servers = []
    server_class = http.server.HTTPServer
    def server(*args, **kwargs):
        value = server_class(*args, **kwargs)
        servers.append(value)
        return value
    monkeypatch.setattr(http.server, 'HTTPServer', server)
    def open_authorization(url):
        query = parse_qs(urlsplit(url).query)
        authorization_transport.nonce = query['nonce'][0]
        redirect = query['redirect_uri'][0]
        assert urlsplit(redirect).hostname == '127.0.0.1'
        assert urlsplit(redirect).port == servers[0].server_address[1]
        returned = redirect + '?' + urlencode({'state': query['state'][0], 'code': 'offline-code',
                                              'client_id': 'issued-test-client'})
        # 浏览器只访问本地标准库服务器；远端换码与 OIDC 继续走实际 SDK 的离线传输。
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(returned, timeout=3) as response:
            assert response.status == 200 and b'Authorization received' in response.read()
        return True
    monkeypatch.setattr(webbrowser, 'open', open_authorization)
    if not granted: authorization_transport.scope = 'openid profile'
    auth = module.ChatGPTAuth('loopback-account', directory=tmp_path)
    if granted:
        identity = await auth.login(port=0, timeout=5)
        assert identity['subject'] == 'offline-subject' and await auth.access_token() == 'offline-access-0'
    else:
        with pytest.raises(ValueError, match='permission'):
            await auth.login(port=0, timeout=5)
        assert not auth.path.exists()
    assert len(servers) == 1 and servers[0].fileno() == -1
