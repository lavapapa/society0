"""实际 HTTP 传输的有界接纳与慢客户端隔离。"""
import http.client
import json
import socket
import threading
import time
from tests.primary.http_server import running
import pytest
from society0.kernel.storage import StageStore
from society0.kernel.threads import ThreadStore,THREAD_SCHEMA


def request(port,method,params=None):
    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
    connection.request('POST','/',json.dumps({'method':method,'params':params or {}}),{'Content-Type':'application/json'})
    response=connection.getresponse();result=json.loads(response.read());status=response.status
    connection.close();return status,result


def test_http_slow_body_saturation_disconnect_and_final_wire_bytes(tmp_path):
    from society0.kernel.observation import ObservationService,make_server
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        threads.append_message(tid,{'role':'user','content':'汉字\n\"🙂'*3000})
        with ObservationService(store.path) as service:
            server=make_server(service,port=0,capacity=2,timeout=.5)
            context=running(server);port,state=context.__enter__()
            clients=[]
            try:
                for _ in range(2):
                    client=socket.create_connection(('127.0.0.1',port));clients.append(client)
                    client.sendall(b'POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 100\r\n\r\n{')
                deadline=time.monotonic()+2
                while state['active']!=2:
                    assert time.monotonic()<deadline;time.sleep(.005)
                start=time.monotonic();status,result=request(port,'status')
                assert status==503 and result['error']['code']=='server_busy'
                assert time.monotonic()-start<.4
                for client in clients:client.close()
                while state['active']:
                    assert time.monotonic()<deadline;time.sleep(.005)
                status,page=request(port,'thread_tail',{'thread_id':tid,'max_bytes':1024})
                assert status==200 and len(json.dumps(page,ensure_ascii=False,separators=(',',':')).encode())<=1024
                status,error=request(port,'status',[])
                assert status==200  # helper's empty params are normalized to an object
                connection=http.client.HTTPConnection('127.0.0.1',port)
                connection.request('POST','/',json.dumps({'method':'status','params':[]}))
                assert connection.getresponse().status==400;connection.close()
            finally:
                for client in clients:client.close()
                context.__exit__(None,None,None)


def test_http_paused_large_response_does_not_block_status(tmp_path):
    from society0.kernel.observation import ObservationService,make_server
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        threads.append_message(tid,{'role':'user','content':'大正文🙂'*700000})
        with ObservationService(store.path) as service:
            server=make_server(service,port=0,capacity=2,timeout=.3)
            context=running(server);port,state=context.__enter__()
            client=socket.socket();client.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,1024)
            client.connect(('127.0.0.1',port))
            data=json.dumps({'method':'read_thread_payload','params':{'reference':{'run_id':store.run_id,'thread_id':tid,'seq':2},'size':8388608,'max_bytes':12582912}}).encode()
            try:
                client.sendall(b'POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: '+str(len(data)).encode()+b'\r\n\r\n'+data)
                deadline=time.monotonic()+2
                while state['active']==0:
                    assert time.monotonic()<deadline;time.sleep(.005)
                start=time.monotonic();status,value=request(port,'status')
                assert status==200 and value['run_id']==store.run_id
                assert time.monotonic()-start<.5
                client.close()
                while state['active']:
                    assert time.monotonic()<deadline;time.sleep(.005)
            finally:
                client.close();context.__exit__(None,None,None)


@pytest.mark.asyncio
async def test_raw_request_cancel_keeps_capacity_until_sync_query_finishes():
    import asyncio
    import httpx
    from society0.kernel.observation import make_app
    class Service:
        def __init__(self):self.entered=threading.Event();self.release=threading.Event();self.calls=0;self.closed=False
        def call(self,*args):
            self.calls+=1
            if self.calls==1:self.entered.set();assert self.release.wait(3)
            assert not self.closed
            return {'ok':True}
        def close(self):self.closed=True
    service=Service();app=make_app(service,capacity=1)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline') as client:
        task=asyncio.create_task(client.post('/',json={'method':'status'}))
        try:
            while not service.entered.is_set():await asyncio.sleep(0)
            task.cancel();await asyncio.sleep(0);task.cancel()
            response=await client.post('/',json={'method':'status'})
            assert response.status_code==503 and service.calls==1
            assert not task.done()
        finally:
            service.release.set();await asyncio.gather(task,return_exceptions=True)


def test_uvicorn_graceful_timeout_waits_for_real_query_before_service_close():
    from society0.kernel.observation import make_server
    class Service:
        def __init__(self):self.entered=threading.Event();self.release=threading.Event();self.finished=False;self.closed=False
        def call(self,*args):
            self.entered.set();assert self.release.wait(3)
            assert not self.closed;self.finished=True;return {'ok':True}
        def close(self):assert self.finished;self.closed=True
    service=Service();server=make_server(service,capacity=1,timeout=.1)
    with running(server) as (port,state):
        results=[]
        def query():
            try:results.append(request(port,'status'))
            except (OSError,http.client.HTTPException,json.JSONDecodeError) as error:
                results.append(('disconnected',type(error).__name__))
        caller=threading.Thread(target=query);caller.start()
        try:
            assert service.entered.wait(2)
            server.should_exit=True
            time.sleep(.35)
            assert not service.closed and state['active']==1
        finally:service.release.set();caller.join(3)
    assert service.finished and service.closed and state['active']==0
    assert len(results)==1 and (results[0][0]==200 or results[0][0]=='disconnected')


@pytest.mark.asyncio
@pytest.mark.parametrize('error_type',[OSError,RuntimeError])
async def test_cancelled_sync_query_failure_keeps_cancel_with_original_cause(error_type):
    import asyncio
    import httpx
    from society0.kernel.observation import make_app
    class Service:
        def __init__(self):self.entered=threading.Event();self.release=threading.Event()
        def call(self,*args):self.entered.set();assert self.release.wait(3);raise error_type('query fixture failure')
        def close(self):pass
    service=Service();app=make_app(service,capacity=1)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline') as client:
        task=asyncio.create_task(client.post('/',json={'method':'status'}))
        while not service.entered.is_set():await asyncio.sleep(0)
        task.cancel();await asyncio.sleep(0);task.cancel()
        try:
            assert (await client.post('/',json={'method':'status'})).status_code==503
            assert not task.done()
        finally:service.release.set()
        with pytest.raises(asyncio.CancelledError) as caught:await task
        assert isinstance(caught.value.__cause__,error_type) and str(caught.value.__cause__)=='query fixture failure'
