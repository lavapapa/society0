"""不登录：合成动态 client_id 与 OAuth token endpoint 的纯本地传输回放。"""
import asyncio,json,socket
from urllib.parse import parse_qs,urlparse
import httpx2
from authlib.integrations.httpx_client import AsyncOAuth2Client

def deny(*a,**kw):raise AssertionError('Real network disabled')
socket.socket.connect=deny
async def main():
    forms=[]
    def mock(req):
        assert str(req.url)=='https://auth.openai.com/api/accounts/oauth/token'
        form=parse_qs(req.content.decode());forms.append(form)
        assert form['client_id']==['issued-mock-client']
        assert form['resource']==['https://api.openai.com/v1']
        return httpx2.Response(200,json={'access_token':'synthetic-access','refresh_token':'synthetic-rotated-refresh','token_type':'Bearer','expires_in':3600,'scope':'openid resource.invoke chatgpt.tokens.use.direct'})
    async with AsyncOAuth2Client('dynamic_agent_client',token_endpoint_auth_method='none',scope='openid profile email offline_access resource.invoke chatgpt.tokens.use.direct',redirect_uri='http://127.0.0.1:1455/auth/callback',code_challenge_method='S256',transport=httpx2.MockTransport(mock)) as auth:
        url,state=auth.create_authorization_url('https://auth.openai.com/api/accounts/authorize',code_verifier='v'*48,nonce='synthetic-nonce',resource='https://api.openai.com/v1',ext_agent_host_id='urn:uuid:00000000-0000-4000-8000-000000000001',agent_name_hint='Society0')
        qs=parse_qs(urlparse(url).query)
        assert qs['client_id']==['dynamic_agent_client'] and qs['code_challenge_method']==['S256']
        # 回调返回 issued ID；配置新 client 后以原 state/verifier 换码。
        auth.client_id='issued-mock-client'
        await auth.fetch_token('https://auth.openai.com/api/accounts/oauth/token',authorization_response='http://127.0.0.1:1455/auth/callback?code=synthetic-code&state='+state,code_verifier='v'*48,resource='https://api.openai.com/v1')
        await auth.refresh_token('https://auth.openai.com/api/accounts/oauth/token',refresh_token='synthetic-refresh',resource='https://api.openai.com/v1')
        assert [f['grant_type'][0] for f in forms]==['authorization_code','refresh_token']
        assert forms[0]['code_verifier']==['v'*48]
        print(json.dumps({'network':'MockTransport only; sockets blocked','authlib_dynamic_client_rebind':True,'authorization_PKCE_library_generated':True,'token_exchange_issued_client':True,'refresh_issued_client':True,'refresh_resource':True,'OIDC_identity_validation_tested':False,'credential_persistence_tested':False,'grant_types':[f['grant_type'][0] for f in forms]},indent=2))
asyncio.run(main())
