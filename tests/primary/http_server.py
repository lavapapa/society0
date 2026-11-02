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
    # 启动等待用真实事件加宽裕上限：共享主机上 5 秒会把正常启动判成失败，
    # 而“线程已死”仍立即失败，契约不变。
    deadline=time.monotonic()+60
    try:
        while not server.started:
            assert thread.is_alive(), '服务器线程在启动前退出'
            assert time.monotonic()<deadline, '服务器启动超过 60 秒'
            time.sleep(.005)
        yield port,state
    finally:
        server.should_exit=True;thread.join(30);listener.close()
        assert not thread.is_alive()
