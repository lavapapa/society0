"""真实 SQL 下推、授权分页和大正文范围读取。"""
import json

import pytest

from society0.kernel.interaction import InteractionScope, Moment, Query, Unavailable
from society0.kernel.storage import StageReader, StageStore
from society0.kernel.information_sql import DatasetSpec, DocumentSpec, SQLInformation


def setup(tmp_path, count=20):
    store = StageStore.create(tmp_path / 'run', [
        'CREATE TABLE orders(id INTEGER PRIMARY KEY,owner TEXT NOT NULL,price INTEGER NOT NULL,created INTEGER NOT NULL)',
        'CREATE INDEX orders_owner_created ON orders(owner,created,id)',
        'CREATE INDEX orders_owner_id ON orders(owner,id)',
        'CREATE TABLE counts(owner TEXT PRIMARY KEY NOT NULL,n INTEGER NOT NULL)',
        'CREATE TABLE docs(id INTEGER PRIMARY KEY,owner TEXT NOT NULL,body BLOB NOT NULL)',
    ], initialize=lambda w: (
        w.executemany('INSERT INTO orders VALUES(?,?,?,?)', [(i, 'alice' if i % 2 == 0 else 'bob', i % 3, i // 3) for i in range(count)]),
        w.executemany('INSERT INTO counts VALUES(?,?)', [('alice', (count + 1)//2), ('bob', count//2)]),
        w.execute('INSERT INTO docs VALUES(1,?,?)', ('alice', ('甲🙂' * 200000).encode())),
    ))
    def where(scope): return ('owner=?', (scope.actor,))
    specs = {
        'orders': DatasetSpec('orders', 'id', ('id', 'price', 'created'), authorize=where,
                              order_fields=('id', 'price', 'created'),
                              base_count=lambda scope: ('SELECT n FROM counts WHERE owner=?', (scope.actor,))),
        'docs': DocumentSpec('docs', 'id', 'body', authorize=where),
    }
    return store, SQLInformation('market', StageReader(store.path), specs)


@pytest.mark.asyncio
async def test_authorized_projection_filter_count_and_refs(tmp_path):
    store, provider = setup(tmp_path)
    try:
        with InteractionScope('alice', Moment(0, 'read')) as scope:
            page = await provider.query(scope, '/market/orders', Query(fields=('id', 'price'), filters=(('price','eq',1),), limit=2))
            assert page.total == 3
            assert page.items[0] == {'id': 4, 'price': 1, 'ref': provider.ref('/market/orders/4')}
            assert page.next_cursor is not None
            second = await provider.query(scope, '/market/orders', Query(fields=('id','price'), filters=(('price','eq',1),), limit=2, cursor=json.loads(json.dumps(page.next_cursor))))
            assert second.items[0]['id'] == 16 and second.next_cursor is None
        with InteractionScope('bob', Moment(0, 'read')) as scope:
            assert (await provider.query(scope, '/market/orders', Query())).total == 10
    finally: store.close()


@pytest.mark.asyncio
async def test_mixed_direction_keyset_equal_values_and_revision_binding(tmp_path):
    store, provider = setup(tmp_path)
    try:
        with InteractionScope('alice', Moment(0, 'read')) as scope:
            query = dict(fields=('id','price'), order=(('price','desc'),('id','asc')), limit=2)
            ids, cursor = [], None
            while True:
                page = await provider.query(scope, '/market/orders', Query(**query, cursor=cursor))
                ids.extend(row['id'] for row in page.items)
                cursor = page.next_cursor
                if cursor is None: break
            expected = sorted(range(0,20,2), key=lambda i: (-(i%3),i))
            assert ids == expected
            first = await provider.query(scope, '/market/orders', Query(limit=1))
            store.transaction(lambda w: w.execute('UPDATE orders SET price=8 WHERE id=0'))
            with pytest.raises(ValueError, match='revision|cursor'):
                await provider.query(scope, '/market/orders', Query(limit=1,cursor=first.next_cursor))
    finally: store.close()


@pytest.mark.asyncio
async def test_cursor_bound_to_actor_route_projection_and_filters(tmp_path):
    store, provider = setup(tmp_path)
    try:
        alice = InteractionScope('alice', Moment(0,'p'))
        cursor = (await provider.query(alice, '/market/orders', Query(limit=1))).next_cursor
        for scope, query in [(InteractionScope('bob',Moment(0,'p')),Query(cursor=cursor)),
                             (alice,Query(fields=('price',),cursor=cursor)),
                             (alice,Query(filters=(('price','eq',1),),cursor=cursor))]:
            with pytest.raises(ValueError, match='cursor'):
                await provider.query(scope, '/market/orders', query)
    finally: store.close()


@pytest.mark.asyncio
async def test_document_blob_range_and_authorization(tmp_path):
    store, provider = setup(tmp_path)
    try:
        with InteractionScope('alice',Moment(0,'p')) as scope:
            chunk = await provider.read(scope, '/market/docs/1', offset=1399990, size=10)
            assert chunk.total_bytes == 1400000 and len(chunk.data) == 10
            assert chunk.data == ('甲🙂' * 200000).encode()[-10:]
            assert chunk.next_offset is None
        with InteractionScope('bob',Moment(0,'p')) as scope:
            with pytest.raises(Unavailable): await provider.read(scope, '/market/docs/1',offset=0,size=4)
    finally: store.close()


@pytest.mark.asyncio
async def test_sample_is_repeatable_authorized_and_exposes_population(tmp_path):
    store, provider = setup(tmp_path, 200)
    try:
        with InteractionScope('alice',Moment(0,'p')) as scope:
            query = Query(fields=('id',),limit=5,sample_seed=17)
            a = await provider.query(scope,'/market/orders',query)
            b = await provider.query(scope,'/market/orders',query)
            assert a.items == b.items and len(a.items) == a.total == 5
            assert a.population_total == 100 and a.next_cursor is None
            assert all(row['id']%2 == 0 for row in a.items)
    finally: store.close()


def test_registered_schema_rejects_nullable_order_and_nonblob_documents(tmp_path):
    with StageStore.create(tmp_path / 'run', ['CREATE TABLE bad(id INTEGER PRIMARY KEY,sort INTEGER,body TEXT)']) as store:
        reader = StageReader(store.path)
        with pytest.raises(ValueError,match='nonnull'):
            SQLInformation('m',reader,{'bad':DatasetSpec('bad','id',('id','sort'),order_fields=('sort',))})
        with pytest.raises(ValueError,match='BLOB'):
            SQLInformation('m',reader,{'bad':DocumentSpec('bad','id','body')})


@pytest.mark.asyncio
async def test_fields_and_operations_are_registered_not_arbitrary_sql(tmp_path):
    store, provider = setup(tmp_path)
    try:
        scope = InteractionScope('alice',Moment(0,'p'))
        for query in [Query(fields=('owner',)),Query(filters=(('id','raw_sql','1 OR 1=1'),)), Query(order=(('owner','asc'),))]:
            with pytest.raises(ValueError): await provider.query(scope,'/market/orders',query)
        assert store.read(lambda r:r.query('SELECT COUNT(*) FROM orders')) == [(20,)]
    finally: store.close()


@pytest.mark.asyncio
async def test_large_history_hot_query_vm_and_materialized_bytes_remain_bounded(tmp_path):
    measurements = []
    for count in (1000, 10000):
        store, provider = setup(tmp_path / str(count), count)
        base_read = provider.reader.read
        metrics = {'vm': 0, 'rows': 0, 'bytes': 0, 'sql': []}
        def measured(callback, **kwargs):
            def instrument(view):
                def progress():
                    metrics['vm'] += 1
                    return False
                view._connection.set_progress_handler(progress, 1)
                query = view.query
                def counted(sql, bindings=(), **options):
                    metrics['sql'].append(sql)
                    rows = query(sql, bindings, **options)
                    metrics['rows'] += len(rows)
                    metrics['bytes'] += sum(len(value) if isinstance(value,(str,bytes)) else 8 for row in rows for value in row)
                    return rows
                view.query = counted
                return callback(view)
            return base_read(instrument, **kwargs)
        provider.reader.read = measured
        try:
            scope = InteractionScope('alice',Moment(0,'p'))
            page = await provider.query(scope,'/market/orders',Query(fields=('id',),limit=5))
            assert page.total == count//2 and len(page.items) == 5
            measurements.append(dict(metrics))
            assert not any('COUNT(*)' in sql for sql in metrics['sql'])
            metrics.update(vm=0,rows=0,bytes=0,sql=[])
            chunk = await provider.read(scope,'/market/docs/1',offset=1000000,size=64)
            assert len(chunk.data)==64 and metrics['bytes']<=100
        finally: store.close()
    print(json.dumps({'history_sizes':[1000,10000],'measurements':measurements}))
    assert measurements[1]['vm'] <= measurements[0]['vm'] * 2
    assert measurements[1]['rows'] == measurements[0]['rows'] == 7
    assert measurements[1]['bytes'] == measurements[0]['bytes']


@pytest.mark.asyncio
async def test_sampling_streams_only_keys_then_fetches_selected_projection(tmp_path):
    store, provider = setup(tmp_path,200)
    base = provider.reader.read
    queries = []
    def measured(callback, **kwargs):
        def inspect(view):
            query, iterate = view.query, view.iter_query
            def q(sql,*args,**opts):
                queries.append(('query',sql))
                return query(sql,*args,**opts)
            def it(sql,*args,**opts):
                queries.append(('stream',sql))
                return iterate(sql,*args,**opts)
            view.query, view.iter_query = q,it
            return callback(view)
        return base(inspect,**kwargs)
    provider.reader.read = measured
    try:
        page = await provider.query(InteractionScope('alice',Moment(0,'p')),'/market/orders',Query(limit=5,sample_seed=2))
        assert page.population_total==100
        assert [sql for kind,sql in queries if kind=='stream'] == ['SELECT "id" FROM "orders" WHERE (owner=?) ORDER BY "id"']
        assert len([sql for kind,sql in queries if kind=='query'])==2
    finally:store.close()


@pytest.mark.asyncio
async def test_shared_environment_example_checkpoint_and_workspace(tmp_path):
    import runpy
    from pathlib import Path
    pytest.importorskip('bashkit')
    example = runpy.run_path(str(Path(__file__).parents[2] / 'examples/core_next/shared_environment.py'))
    report = await example['demonstrate'](tmp_path / 'example', history=1000)
    assert report['restored_equal'] and report['complete_step']==1
    assert report['artifact_count'] >= 10
    assert [actor['actor'] for actor in report['actors']] == ['alice','bob']
    assert any(command['stdout']=='12\n' for command in report['actors'][0]['commands'])


@pytest.mark.asyncio
async def test_mixed_indexed_seek_does_not_scan_prior_equal_rank(tmp_path):
    with StageStore.create(tmp_path/'mixed',[
        'CREATE TABLE items(id INTEGER PRIMARY KEY,owner TEXT NOT NULL,rank INTEGER NOT NULL)',
        'CREATE INDEX mixed_order ON items(owner,rank DESC,id ASC)'],
        initialize=lambda w:w.executemany('INSERT INTO items VALUES(?,?,?)',((i,'a',0) for i in range(10000)))) as store:
        reader=StageReader(store.path)
        provider=SQLInformation('m',reader,{'items':DatasetSpec('items','id',('id','rank'),order_fields=('rank',),
            authorize=lambda s:('owner=?',(s.actor,)),base_count=lambda s:('SELECT 10000',()))},max_page_size=10000)
        scope=InteractionScope('a',Moment(0,'p'))
        original=reader.read
        measured=[]
        for position in (100,9000):
            cursor=(await provider.query(scope,'/m/items',Query(order=(('rank','desc'),('id','asc')),limit=position))).next_cursor
            count=[0]
            def read(callback,**kwargs):
                def wrapped(view):
                    view._connection.set_progress_handler(lambda:count.__setitem__(0,count[0]+1) or False,1)
                    return callback(view)
                return original(wrapped,**kwargs)
            reader.read=read
            page=await provider.query(scope,'/m/items',Query(order=(('rank','desc'),('id','asc')),limit=1,cursor=cursor))
            reader.read=original
            assert page.items[0]['id']==position
            measured.append(count[0])
        assert measured[1] < measured[0]*2, measured
