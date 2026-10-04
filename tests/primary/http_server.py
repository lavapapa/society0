"""真实 Uvicorn socket 测试夹具，生命周期仍由原生 Server 拥有。"""
from contextlib import contextmanager
import socket
import threading
import time


@contextmanager
def running(server):
    listener=socket.socket();listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
    state={'active':0};app=server.config.app
    async def observed(scope,receive,send):
        if scope['type']!='http':return await app(scope,receive,send)
        state['active']+=1
        try:return await app(scope,receive,send)
        finally:state['active']-=1
    server.config.app=observed
    thread=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);thread.start()
    deadline=time.monotonic()+5
    try:
        while not server.started:
            assert thread.is_alive() and time.monotonic()<deadline
            time.sleep(.005)
        yield port,state
    finally:
        server.should_exit=True;thread.join(5);listener.close()
        assert not thread.is_alive()
