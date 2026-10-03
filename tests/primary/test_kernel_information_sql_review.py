"""存储作者对 SQL 信息提供者的独立反例。"""
import apsw
import pytest
from society0.kernel.storage import StageStore, StageReader
from society0.kernel.interaction import InteractionScope, Moment, Query
from society0.kernel.information_sql import SQLInformation, DocumentSpec, DatasetSpec


@pytest.mark.asyncio
async def test_review_small_blob_range_does_not_materialize_whole_document(tmp_path):
    with StageStore.create(tmp_path/'run', ['CREATE TABLE docs(id INTEGER PRIMARY KEY,body BLOB NOT NULL)'],
                           initialize=lambda w:w.execute('INSERT INTO docs VALUES(1,zeroblob(?))',(32*1024*1024,))) as store:
        provider = SQLInformation('m',StageReader(store.path),{'docs':DocumentSpec('docs','id','body')})
        apsw.status(apsw.SQLITE_STATUS_MEMORY_USED,True)
        before = apsw.status(apsw.SQLITE_STATUS_MEMORY_USED)[0]
        chunk = await provider.read(InteractionScope('a',Moment(0,'read')),'/m/docs/1',offset=16*1024*1024,size=64)
        peak = apsw.status(apsw.SQLITE_STATUS_MEMORY_USED)[1]
        assert chunk.data == b'\0'*64
        assert peak-before < 8*1024*1024, {'sqlite_temporary_peak':peak-before,'requested_bytes':64}


@pytest.mark.asyncio
async def test_review_cursor_is_bound_to_moment_when_authorization_changes(tmp_path):
    with StageStore.create(tmp_path/'run', ['CREATE TABLE items(id INTEGER PRIMARY KEY,phase TEXT NOT NULL)'],
                           initialize=lambda w:w.executemany('INSERT INTO items VALUES(?,?)',[(1,'morning'),(2,'morning'),(3,'night'),(4,'night')])) as store:
        provider = SQLInformation('m',StageReader(store.path),{'items':DatasetSpec('items','id',('id',),authorize=lambda scope:('phase=?',(scope.moment.phase,)))})
        cursor = (await provider.query(InteractionScope('a',Moment(0,'morning')),'/m/items',Query(limit=1))).next_cursor
        with pytest.raises(ValueError,match='cursor'):
            await provider.query(InteractionScope('a',Moment(0,'night')),'/m/items',Query(limit=1,cursor=cursor))


@pytest.mark.asyncio
async def test_review_blob_rowid_alias_selection_is_case_insensitive(tmp_path):
    with StageStore.create(tmp_path/'run', ['CREATE TABLE docs(id INTEGER PRIMARY KEY,RowID INTEGER NOT NULL,owner TEXT NOT NULL,body BLOB NOT NULL)'],
                           initialize=lambda w:w.executemany('INSERT INTO docs VALUES(?,?,?,?)',[(1,2,'alice',b'alice'),(2,1,'bob',b'bob')])) as store:
        provider = SQLInformation('m',StageReader(store.path),{'docs':DocumentSpec('docs','id','body',authorize=lambda scope:('owner=?',(scope.actor,)))})
        chunk = await provider.read(InteractionScope('alice',Moment(0,'read')),'/m/docs/1',size=64)
        assert chunk.data == b'alice'


@pytest.mark.asyncio
async def test_review_indexed_keyset_seeks_past_large_equal_prefix(tmp_path):
    with StageStore.create(tmp_path/'run',[
        'CREATE TABLE items(id INTEGER PRIMARY KEY,owner TEXT NOT NULL,rank INTEGER NOT NULL)',
        'CREATE INDEX ordered_items ON items(owner,rank,id)'],
        initialize=lambda w:w.executemany('INSERT INTO items VALUES(?,?,?)',((i,'a',0) for i in range(10000)))) as store:
        reader = StageReader(store.path)
        provider = SQLInformation('m',reader,{'items':DatasetSpec('items','id',('id','rank'),
            order_fields=('rank',),authorize=lambda s:('owner=?',(s.actor,)),base_count=lambda s:('SELECT 10000',()))},max_page_size=10000)
        scope = InteractionScope('a',Moment(0,'read'))
        original = reader.read
        measured = []
        for position in (100,9000):
            cursor = (await provider.query(scope,'/m/items',Query(order=(('rank','asc'),('id','asc')),limit=position))).next_cursor
            steps = [0]
            def trace(callback,**kwargs):
                def wrapped(view):
                    view._connection.set_progress_handler(lambda:steps.__setitem__(0,steps[0]+1) or False,1)
                    return callback(view)
                return original(wrapped,**kwargs)
            reader.read = trace
            page = await provider.query(scope,'/m/items',Query(order=(('rank','asc'),('id','asc')),limit=1,cursor=cursor))
            reader.read = original
            assert page.items[0]['id'] == position
            measured.append(steps[0])
        assert measured[1] < measured[0]*2, measured
