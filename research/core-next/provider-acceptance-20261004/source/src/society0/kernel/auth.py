"""官方 Sign in with ChatGPT：Authlib OAuth、独立账户文件与循环地址回调。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
import uuid

import httpx2
from authlib.common.security import generate_token
from authlib.integrations.httpx_client import AsyncOAuth2Client
from joserfc import jwt
from joserfc.jwk import KeySet

ISSUER = 'https://auth.openai.com'
AUTHORIZE = ISSUER + '/api/accounts/authorize'
TOKEN = ISSUER + '/api/accounts/oauth/token'
RESOURCE = 'https://api.openai.com/v1'
SCOPES = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.credentials-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


@dataclass(frozen=True)
class LoginAttempt:
    url: str
    state: str
    nonce: str
    verifier: str
    redirect_uri: str
    client_id: str
    subject: str | None
    host_id: str


class ChatGPTAuth:
    """一个显式账户别名对应一个已验证 registration；构造不会读文件或联网。"""
    def __init__(self, account='default', *, directory=None):
        if not account or Path(account).name != account or account in ('.', '..'):
            raise ValueError('account must be a filename')
        self.directory = Path(directory or Path.home() / '.config/society0/chatgpt')
        self.path = self.directory / (account + '.json')
        self._mutex = asyncio.Lock()

    @asynccontextmanager
    async def _locked(self):
        async with self._mutex:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(self.directory / '.lock', os.O_CREAT | os.O_RDWR, 0o600)
            try:
                while True:
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError: await asyncio.sleep(0.05)
                yield
            finally: os.close(descriptor)

    async def begin(self, redirect_uri):
        parsed = urlsplit(redirect_uri)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.path != '/auth/callback':
            raise ValueError('callback must use http://127.0.0.1:PORT/auth/callback')
        async with self._locked():
            host_path = self.directory / 'host.json'
            if not host_path.exists(): _save(host_path, {'id': 'urn:uuid:' + str(uuid.uuid4())})
            host = json.loads(host_path.read_text())['id']
            previous = json.loads(self.path.read_text()) if self.path.exists() else {}
        client_id = previous.get('client_id', 'dynamic_agent_client')
        nonce, verifier = generate_token(48), generate_token(64)
        extra = {'resource': RESOURCE, 'ext_agent_host_id': host, 'nonce': nonce}
        if client_id == 'dynamic_agent_client': extra['agent_name_hint'] = 'Society0'
        else:
            extra['id_token_hint'] = previous['token']['id_token']
            if previous.get('email'): extra['login_hint'] = previous['email']
        async with AsyncOAuth2Client(client_id, scope=SCOPES, redirect_uri=redirect_uri,
                                     code_challenge_method='S256', token_endpoint_auth_method='none') as client:
            url, state = client.create_authorization_url(AUTHORIZE, code_verifier=verifier, **extra)
        return LoginAttempt(url, state, nonce, verifier, redirect_uri, client_id, previous.get('subject'), host)

    async def _identity(self, token, client_id, nonce=None):
        async with httpx2.AsyncClient(timeout=30) as client:
            discovery = await client.get(ISSUER + '/.well-known/openid-configuration')
            discovery.raise_for_status()
            config = discovery.json()
            if config['issuer'] != ISSUER: raise ValueError('unexpected OIDC issuer')
            response = await client.get(config['jwks_uri'])
            response.raise_for_status()
        decoded = jwt.decode(token['id_token'], KeySet.import_key_set(response.json()), algorithms=['RS256'])
        claims = {'iss': {'essential': True, 'value': ISSUER}, 'aud': {'essential': True, 'value': client_id},
                  'exp': {'essential': True}, 'sub': {'essential': True}}
        if nonce is not None: claims['nonce'] = {'essential': True, 'value': nonce}
        jwt.JWTClaimsRegistry(**claims).validate(decoded.claims)
        return decoded.claims

    async def callback(self, attempt, callback_url):
        actual = urlsplit(callback_url)
        expected = urlsplit(attempt.redirect_uri)
        if (actual.scheme, actual.netloc, actual.path) != (expected.scheme, expected.netloc, expected.path):
            raise ValueError('callback URI differs from authorization')
        query = parse_qs(actual.query)
        if query.get('state') != [attempt.state]: raise ValueError('authorization state mismatch')
        if 'error' in query: raise ValueError('ChatGPT authorization was not granted')
        client_id = query.get('client_id', [attempt.client_id])
        if len(client_id) != 1 or client_id[0] == 'dynamic_agent_client': raise ValueError('issued client ID missing')
        client_id = client_id[0]
        if attempt.client_id != 'dynamic_agent_client' and client_id != attempt.client_id:
            raise ValueError('reauthorization changed registration')
        async with AsyncOAuth2Client(client_id, state=attempt.state, redirect_uri=attempt.redirect_uri,
                                     token_endpoint_auth_method='none') as client:
            token = await client.fetch_token(TOKEN, authorization_response=callback_url,
                                            code_verifier=attempt.verifier, resource=RESOURCE)
        identity = await self._identity(token, client_id, attempt.nonce)
        if attempt.subject is not None and identity['sub'] != attempt.subject:
            raise ValueError('reauthorization changed account')
        self._permission(token)
        record = {'client_id': client_id, 'subject': identity['sub'], 'email': identity.get('email'),
                  'host_id': attempt.host_id, 'token': dict(token)}
        async with self._locked(): _save(self.path, record)
        return {'subject': record['subject'], 'email': record['email'], 'client_id': client_id}

    @staticmethod
    def _permission(token):
        if 'chatgpt.tokens.use.direct' not in token.get('scope', '').split():
            raise ValueError('ChatGPT plan usage permission is absent')

    async def access_token(self, *, force_refresh=False):
        async with self._locked():
            record = json.loads(self.path.read_text())
            token = record['token']
            if force_refresh or token.get('expires_at', 0) <= time.time() + 60:
                async with AsyncOAuth2Client(record['client_id'], token=token,
                                             token_endpoint_auth_method='none') as client:
                    refreshed = dict(await client.refresh_token(TOKEN, resource=RESOURCE))
                replacement = {**token, **refreshed}
                if refreshed.get('id_token'):
                    identity = await self._identity(replacement, record['client_id'])
                    if identity['sub'] != record['subject']: raise ValueError('refresh changed account')
                self._permission(replacement)
                record['token'] = replacement
                _save(self.path, record)
                token = replacement
            self._permission(token)
            return token['access_token']

    async def login(self, *, port=1455, open_browser=True, timeout=300):
        """先监听再打开浏览器；重定向原文仅保留在本次内存中。"""
        loop = asyncio.get_running_loop()
        returned = loop.create_future()
        from http.server import HTTPServer, BaseHTTPRequestHandler
        from threading import Thread
        def deliver(path):
            if not returned.done(): returned.set_result(path)
        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if urlsplit(self.path).path != '/auth/callback':
                    self.send_error(404)
                    return
                loop.call_soon_threadsafe(deliver, self.path)
                body = b'Authorization received. Return to Society0 to see the result.'
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, format, *args): pass
            def setup(self):
                super().setup()
                self.connection.settimeout(10)
        server = HTTPServer(('127.0.0.1', port), CallbackHandler)
        worker = Thread(target=server.serve_forever, kwargs={'poll_interval': .1})
        worker.start()
        try:
            origin = f'http://127.0.0.1:{server.server_address[1]}'
            attempt = await self.begin(origin + '/auth/callback')
            if open_browser:
                import webbrowser
                webbrowser.open(attempt.url)
                print('Continue authorization in your browser.')
            else:
                print('Open this private authorization URL in your browser: ' + attempt.url)
            path = await asyncio.wait_for(returned, timeout)
            return await self.callback(attempt, origin + path)
        finally:
            # 标准库服务器拥有其线程；退出前关闭监听并等待请求处理结束。
            server.shutdown()
            server.server_close()
            worker.join()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Society0 ChatGPT subscription authorization')
    parser.add_argument('command', choices=('login', 'refresh'))
    parser.add_argument('--account', default='default')
    parser.add_argument('--directory')
    parser.add_argument('--port', type=int, default=1455)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    auth = ChatGPTAuth(args.account, directory=args.directory)
    if args.command == 'login':
        result = asyncio.run(auth.login(port=args.port, open_browser=not args.no_browser))
        print('ChatGPT account authorized: ' + str(result['email'] or result['subject']))
    else:
        asyncio.run(auth.access_token(force_refresh=True))
        print('ChatGPT credentials refreshed.')


if __name__ == '__main__': main()
