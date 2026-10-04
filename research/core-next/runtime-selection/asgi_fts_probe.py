"""Starlette 独立请求生命周期及原生 SQLite FTS5 最小 API 验证。"""
from contextlib import asynccontextmanager
from importlib.metadata import version
import json
import sqlite3
import threading
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

state=[]
@asynccontextmanager
async def lifespan(app):
    state.append('opened')
    yield
    state.append('closed')

def status(request):
    # 连接的建立、查询和关闭均在同一次同步 worker 调用内。
    with sqlite3.connect(':memory:') as db:
        value=db.execute('select 1').fetchone()[0]
    return JSONResponse({'value':value,'worker_thread':threading.get_ident()})

app=Starlette(routes=[Route('/status',status)],lifespan=lifespan)
with TestClient(app) as client:
    assert state==['opened']
    response=client.get('/status')
    assert response.status_code==200 and response.json()['value']==1
assert state==['opened','closed']
with sqlite3.connect(':memory:') as db:
    db.execute('CREATE VIRTUAL TABLE templates USING fts5(name,description,tokenize="unicode61")')
    db.executemany('INSERT INTO templates VALUES(?,?)',[('publish','publish a market message'),('read','read a market message')])
    found=db.execute('SELECT name,bm25(templates) FROM templates WHERE templates MATCH ? ORDER BY bm25(templates),rowid',('publish',)).fetchall()
    assert [x[0] for x in found]==['publish']
print(json.dumps({'starlette':version('starlette'),'uvicorn_installed':version('uvicorn'),
                  'asgi_client_http_status':response.status_code,'lifespan':state,
                  'sqlite_same_call_thread':True,'sqlite_version':sqlite3.sqlite_version,
                  'fts5_ranked_names':[x[0] for x in found],
                  'uvicorn_real_socket_test_performed':False},indent=2))
