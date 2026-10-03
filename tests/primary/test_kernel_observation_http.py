"""实际 HTTP 传输的有界接纳与慢客户端隔离。"""
import http.client
import json
import socket
import threading
import time
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
            runner=threading.Thread(target=server.serve_forever);runner.start();port=server.server_address[1]
            clients=[]
            try:
                for _ in range(2):
                    client=socket.create_connection(('127.0.0.1',port));clients.append(client)
                    client.sendall(b'POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 100\r\n\r\n{')
                deadline=time.monotonic()+2
                while server.active_requests!=2:
                    assert time.monotonic()<deadline;time.sleep(.005)
                start=time.monotonic();status,result=request(port,'status')
                assert status==503 and result['error']['code']=='server_busy'
                assert time.monotonic()-start<.4
                for client in clients:client.close()
                while server.active_requests:
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
                server.shutdown();server.server_close();runner.join()


def test_http_paused_large_response_does_not_block_status(tmp_path):
    from society0.kernel.observation import ObservationService,make_server
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        threads.append_message(tid,{'role':'user','content':'大正文🙂'*700000})
        with ObservationService(store.path) as service:
            server=make_server(service,port=0,capacity=2,timeout=.3)
            runner=threading.Thread(target=server.serve_forever);runner.start();port=server.server_address[1]
            client=socket.socket();client.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,1024)
            client.connect(('127.0.0.1',port))
            data=json.dumps({'method':'read_thread_payload','params':{'reference':{'run_id':store.run_id,'thread_id':tid,'seq':2},'size':8388608,'max_bytes':12582912}}).encode()
            try:
                client.sendall(b'POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: '+str(len(data)).encode()+b'\r\n\r\n'+data)
                deadline=time.monotonic()+2
                while server.active_requests==0:
                    assert time.monotonic()<deadline;time.sleep(.005)
                start=time.monotonic();status,value=request(port,'status')
                assert status==200 and value['run_id']==store.run_id
                assert time.monotonic()-start<.5
                client.close()
                while server.active_requests:
                    assert time.monotonic()<deadline;time.sleep(.005)
            finally:
                client.close();server.shutdown();server.server_close();runner.join()
