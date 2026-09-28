"""Thread 实现者对独立存储实现的交叉审查复现。"""
import pytest

from society0.incremental_checkpoint import V4CheckpointStore


def test_review_export_rejects_changed_base_checkpoint_identity(tmp_path):
    source_path = tmp_path / 'source'
    source = V4CheckpointStore(source_path)
    original = source.publish_root(({'sequence': 0, 'path': ['x'], 'operation': 'set', 'value': 1},), metadata={'run_id': 'original'})
    target = V4CheckpointStore(tmp_path / 'target')
    target.publish_root((), metadata={'run_id': 'target', 'base_checkpoint': {
        'source_root': '../source', 'step': 0, 'checkpoint_id': original['checkpoint_id'], 'branch_id': 'main'}})
    source_path.rename(tmp_path / 'old-source')
    substituted = V4CheckpointStore(source_path)
    substituted.publish_root(({'sequence': 0, 'path': ['x'], 'operation': 'set', 'value': 2},), metadata={'run_id': 'different'})
    with pytest.raises(ValueError, match='identity'):
        target.resolve(0)
    with pytest.raises(ValueError, match='identity'):
        target.fork("review-fork", step=0)
    with pytest.raises(ValueError, match='identity'):
        target.restore(0)
    with pytest.raises(ValueError, match='identity'):
        target.export_bundle(tmp_path / 'bundle')
    assert not (tmp_path / 'bundle').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['staging', 'publish'])
async def test_review_failed_pending_epoch_revokes_memory_and_keeps_last_marker(tmp_path, monkeypatch, failure):
    from society0.core_data import World
    from society0.persistence import PersistenceManager
    from society0.incremental_checkpoint import PersistenceSchema, SealedTickDelta
    from society0.state_persistence import persistent_state_schema, replaceable
    from society0 import checkpoint_records
    world = World(event_log_path=str(tmp_path / 'events.jsonl'))
    world.environment_data.update(type='plain', state={'x': 0})
    manager = PersistenceManager(str(tmp_path))
    manager.configure_v4(world, PersistenceSchema.compile(persistent_state_schema(x=replaceable()), root_path=('environment', 'state')), checkpoint_every=2)
    try:
        root = await manager.publish_root(world, object())
        await manager.publish_delta(SealedTickDelta(1, ({'sequence': 0, 'operation': 'set', 'path': ['environment', 'state', 'x'], 'value': 1},), (), write_epoch_ids=('pending-first',)), object())
        assert 'pending-first' in world._committed_memory_epoch_ids
        def fail(*args, **kwargs):
            raise OSError('injected pending epoch failure')
        if failure == 'staging':
            monkeypatch.setattr(checkpoint_records, 'write_records', fail)
        else:
            monkeypatch.setattr(manager._v4_store, 'publish', fail)
        with pytest.raises(OSError, match='injected pending'):
            await manager.publish_delta(SealedTickDelta(2, (), (), write_epoch_ids=('pending-second',)), object())
        assert 'pending-first' not in world._committed_memory_epoch_ids
        assert 'pending-second' not in world._committed_memory_epoch_ids
        assert manager._v4_store.resolve()['checkpoint_id'] == root['checkpoint_id']
        assert manager._v4_store.restore(0)['environment']['state'] == {'x': 0}
        assert not manager._v4_epoch
        assert not manager._v4_staged_records
        assert not list((tmp_path / 'checkpoints' / 'v4' / 'pending').iterdir())
    finally:
        manager.close()
        world.event_logger.close()


def test_review_shared_blocks_merge_remaps_paths_and_byte_ranges(tmp_path):
    import json
    from society0.checkpoint_records import write_records, merge_records, iter_record_bytes, iter_metadata
    records = [
        {'sequence': 0, 'path': ['a', 'x'], 'operation': 'set', 'value': '甲' * 400000},
        {'sequence': 1, 'path': ['b', 'x'], 'operation': 'set', 'value': {'n': 1}},
        {'sequence': 2, 'path': ['b', 'x'], 'operation': 'set', 'value': '乙' * 400000},
        {'sequence': 3, 'path': ['a', 'x'], 'operation': 'set', 'value': {'n': 2}},
    ]
    first, second, merged = [tmp_path / x for x in ('first.sqlite', 'second.sqlite', 'merged.sqlite')]
    write_records(first, records[:2])
    write_records(second, records[2:])
    merge_records(merged, [first, second])
    assert [m['path'] for m in iter_metadata(merged)] == [r['path'] for r in records]
    for expected in records:
        body = b''.join(iter_record_bytes(merged, expected['sequence']))
        assert json.loads(body) == expected
        # 跨共享压缩块边缘保留逐字节边界。
        start = min(1048500, max(0, len(body) - 100))
        assert b''.join(iter_record_bytes(merged, expected['sequence'], offset=start, max_bytes=200)) == body[start:start+200]


def test_review_v2_fast_and_streaming_json_preserve_escaping_and_scalar_keys(tmp_path):
    import json
    from collections import UserDict
    from society0.checkpoint_records import _json_parts, write_records, iter_records, iter_record_bytes
    small={'文\n\"\\\x00😀': [True,None,-0.0,10**120,1.25e-100], 42:'整数键',None:'空键',False:'布尔键',1.5:'小数键'}
    # UserDict 走逐段编码；普通 dict 走有界 C 编码，键转换和正文应一致。
    expected=json.dumps(small,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
    assert b''.join(_json_parts(small))==expected
    assert b''.join(_json_parts(UserDict(small)))==expected
    large={'value': ('汉😀\"\\\n\x00'*50000),'keys':small}
    parts=list(_json_parts(large))
    assert max(map(len,parts))<=65536
    assert json.loads(b''.join(parts))==json.loads(json.dumps(large))
    record={'sequence':0,'path':['state', '😀\n'], 'operation':'set','value':large}
    target=tmp_path/'escaped.sqlite'
    write_records(target,[record])
    raw=b''.join(iter_record_bytes(target,0))
    assert json.loads(raw)==list(iter_records(target))[0]
    for start,size in ((0,1),(1048570,100),(1048576,65537),(len(raw)-11,100),(len(raw),10)):
        assert b''.join(iter_record_bytes(target,0,offset=start,max_bytes=size))==raw[start:start+size]


def test_review_v2_merge_rebuilds_counts_for_typed_paths_and_page_references(tmp_path):
    import json
    from society0.checkpoint_records import write_records,merge_records,read_page,iter_records,iter_record_bytes
    paths=[['root',1],['root','1'],[True,'x'],[1,'x'],['root','文😀']]
    records=[{'sequence':i,'path':paths[i%len(paths)],'operation':'set','value':{'n':i,'text':'正文\n😀'* (10000 if i%2 else 2)}} for i in range(20)]
    sources=[]
    for part in range(4):
        source=tmp_path/f'{part}.sqlite'
        write_records(source,records[part*5:(part+1)*5],pending=part%2==0)
        sources.append(source)
    first=tmp_path/'first.sqlite';write_records(first,[],pending=True)
    combined=tmp_path/'combined.sqlite';merge_records(combined,[first,*sources])
    assert list(iter_records(combined))==records
    for path in paths:
        expected=[r for r in records if json.dumps(r['path'])==json.dumps(path)]
        after=-1;seen=[]
        while True:
            page=read_page(combined,path_filter=path,after_sequence=after,limit=2,max_bytes=512)
            assert page['total']==len(expected) and page['payload_bytes']<=512
            for row in page['records']:
                seen.append(json.loads(b''.join(iter_record_bytes(combined,row['sequence']))) if 'record_ref' in row else row)
            after=page['next_sequence']
            if after is None:break
        assert seen==expected


@pytest.mark.parametrize('mode',['write','merge'])
def test_review_temporary_database_publishes_only_after_close_and_file_fsync(tmp_path, monkeypatch, mode):
    import os
    import sqlite3
    import stat
    from pathlib import Path
    import society0.checkpoint_records as records
    source=tmp_path/'source.sqlite'
    body=[{'sequence':0,'path':['x'],'operation':'set','value':'原文😀'*20000}]
    records.write_records(source,body,pending=True)
    original_source=source.read_bytes()
    events=[]
    connect=sqlite3.connect;fsync=os.fsync;replace=Path.replace
    class Tracked(sqlite3.Connection):
        def __init__(self,*a,**kw):
            self.readonly = kw.get('uri',False)
            super().__init__(*a,**kw)
        def close(self):
            super().close()
            if not self.readonly:
                events.append('close')
    monkeypatch.setattr(records.sqlite3,'connect',lambda *a,**kw:connect(*a,factory=Tracked,**kw))
    def synced(fd):
        fsync(fd)
        events.append('dir_fsync' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file_fsync')
    def renamed(path,destination):
        result=replace(path,destination);events.append('rename');return result
    monkeypatch.setattr(records.os,'fsync',synced)
    monkeypatch.setattr(Path,'replace',renamed)
    target=tmp_path/'target.sqlite'
    if mode=='write':records.write_records(target,body)
    else:records.merge_records(target,[source])
    assert events==['close','file_fsync','rename','dir_fsync']
    assert source.read_bytes()==original_source
    assert list(records.iter_records(target))==body


def test_review_merge_directory_fsync_failure_preserves_marker_and_sources(tmp_path, monkeypatch):
    import os
    import stat
    from society0.incremental_checkpoint import SealedTickDelta
    import society0.checkpoint_records as records
    store=V4CheckpointStore(tmp_path/'run')
    root=store.publish_root([{'sequence':0,'path':['x'],'operation':'set','value':'可信根'}])
    sources=[tmp_path/'first.sqlite',tmp_path/'second.sqlite']
    for i,path in enumerate(sources):
        records.write_records(path,[{'sequence':i,'path':['x'],'operation':'set','value':i}],pending=True)
    original=[p.read_bytes() for p in sources]
    fsync=os.fsync
    target_directory=store.replacements_dir.stat()
    def failed(fd):
        info=os.fstat(fd)
        if stat.S_ISDIR(info.st_mode) and (info.st_dev,info.st_ino)==(target_directory.st_dev,target_directory.st_ino):
            raise OSError('directory durability failed')
        fsync(fd)
    monkeypatch.setattr(records.os,'fsync',failed)
    with pytest.raises(OSError,match='directory durability'):
        store.publish(SealedTickDelta(1,(),()),staged_records=sources)
    assert [p.read_bytes() for p in sources]==original
    assert store.available_steps()==[0]
    assert store.resolve()['checkpoint_id']==root['checkpoint_id']
    assert store.restore(0)['x']=='可信根'


@pytest.mark.parametrize('sizes',[(2000,2200),(2,2)])
def test_review_mixed_page_sources_merge_once_with_exact_records(tmp_path,sizes):
    import sqlite3
    from society0.checkpoint_records import write_records,merge_records,iter_records,read_page
    all_records=[];sources=[];snapshots=[];offset=0
    for part,count in enumerate(sizes):
        rows=[{'sequence':offset+i,'path':['scope',str(i%7)],'operation':'set','value':{'n':offset+i,'text':'正文😀\n'*10}} for i in range(count)]
        path=tmp_path/f'part-{part}.sqlite'
        # 大合并从1024页开始；小合并从4096页开始，覆盖两个重建方向。
        known_count=count if (sum(sizes)>4096 and part==0) or (sum(sizes)<4096 and part==1) else None
        write_records(path,rows,pending=True,record_count=known_count)
        sources.append(path);snapshots.append(path.read_bytes());all_records.extend(rows);offset+=count
    merged=tmp_path/'merged.sqlite';merge_records(merged,sources)
    with sqlite3.connect(merged) as db:
        assert db.execute('PRAGMA page_size').fetchone()[0]==(4096 if sum(sizes)>=4096 else 1024)
    assert list(iter_records(merged))==all_records
    assert [p.read_bytes() for p in sources]==snapshots
    for key in range(7):
        expected=[r for r in all_records if r['path']==['scope',str(key)]]
        page=read_page(merged,path_filter=['scope',str(key)],limit=1)
        assert page['total']==len(expected)
        if expected:assert page['records'][0]==expected[0]


def test_review_identity_and_fork_validate_components_without_reading_values(tmp_path, monkeypatch):
    from society0.incremental_checkpoint import SealedTickDelta
    from society0 import checkpoint_records
    import json
    store = V4CheckpointStore(tmp_path)
    first = store.publish_root([{"sequence": 0, "path": ["x"], "operation": "set", "value": "原文"}])
    second = store.publish(SealedTickDelta(1, ({"sequence": 0, "path": ["x"], "operation": "set", "value": "新文"},), ()))
    def no_values(*args, **kwargs):
        raise AssertionError("identity must not deserialize values")
    monkeypatch.setattr(checkpoint_records, "iter_records", no_values)
    monkeypatch.setattr(V4CheckpointStore, "_restore_chain", no_values)
    assert store.resolve()["checkpoint_id"] == second["checkpoint_id"]
    assert store.fork("good", step=1).resolve()["checkpoint_id"] == second["checkpoint_id"]
    manifest = json.loads((tmp_path / second["manifest_file"]).read_text())
    (tmp_path / manifest["replacement_file"]).unlink()
    assert store.resolve()["checkpoint_id"] == first["checkpoint_id"]
    with pytest.raises(FileNotFoundError):
        store.fork("bad", step=1)


def test_review_identity_resolution_checks_thread_before_returning_marker(tmp_path, monkeypatch):
    from society0.agent.thread_store import AgentThreadStore
    from society0.incremental_checkpoint import SealedTickDelta
    store = V4CheckpointStore(tmp_path)
    first = store.publish_root([{'sequence': 0, 'path': ['x'], 'operation': 'set', 'value': 1}])
    threads = AgentThreadStore(tmp_path)
    thread = threads.open_thread(agent_id='a', checkpoint_step=1, scope={'kind': 'tick', 'id': '1'})
    threads.append_event(thread, 'conversation_message', payload={'role': 'user', 'content': '完整内容'})
    threads.close_thread(thread)
    descriptor = threads.publish_tick_manifest(checkpoint_id='review-1', step=1)
    store.publish(SealedTickDelta(1, (), ()), checkpoint_id='review-1', thread_manifest=descriptor)
    monkeypatch.setattr(V4CheckpointStore, '_restore_chain', lambda *a: (_ for _ in ()).throw(AssertionError('materialized')))
    assert store.resolve()['checkpoint_id'] == 'review-1'
    (tmp_path / descriptor['threads'][thread]['path']).unlink()
    assert store.resolve()['checkpoint_id'] == first['checkpoint_id']
    with pytest.raises(FileNotFoundError):
        store.resolve(1)
    with pytest.raises(FileNotFoundError):
        store.fork('missing-thread', step=1)


def test_review_key_blocks_random_order_typed_fences_and_merge(tmp_path):
    import random
    from society0 import checkpoint_records as records
    keys = [f'{i:05d}:订单:签约\\\n"' + 'field:value/' * 20 for i in range(2300)]
    keys += ['', True, 1, '1', None, '巨大😀' * 40000]
    random.Random(193).shuffle(keys)
    rows = [{'sequence':i,'path':['scope',key],'operation':'set','value':{'order':i,'text':'正文😀\\"\n'*40}} for i,key in enumerate(keys)]
    paths=[]
    for part in range(2):
        path=tmp_path/f'part-{part}.sqlite'
        records.write_records(path, rows[part*1200:(part+1)*1200], pending=bool(part))
        paths.append(path)
    merged=tmp_path/'merged.sqlite'
    records.merge_records(merged,paths)
    metadata=list(records.iter_metadata(merged))
    assert [row['path'] for row in metadata] == [row['path'] for row in rows]
    assert list(records.iter_records(merged)) == rows
    for key in [True,1,'1',None,'',keys[5],keys[-1]]:
        expected=[row for row in rows if type(row['path'][-1]) is type(key) and row['path'][-1]==key]
        page=records.read_page(merged,path_filter=['scope',key],limit=1,max_bytes=1000000)
        assert page['total']==1 and page['records']==expected
    assert records.read_page(merged,path_filter=['scope','missing-key'])['total']==0
    body=b''.join(records.iter_record_bytes(merged,5))
    assert b''.join(records.iter_record_bytes(merged,5,offset=13,max_bytes=97))==body[13:110]


@pytest.mark.parametrize('limit',[1,1000000])
def test_review_page_byte_boundary_and_repeated_path_resume_exactly(tmp_path,limit):
    from society0 import checkpoint_records as records
    rows=[{'sequence':i,'path':['x',i%2],'operation':'set','value':'正文'*15} for i in range(12)]
    path=tmp_path/'paged.sqlite';records.write_records(path,rows)
    budget=sum(len(b''.join(records.iter_record_bytes(path,i))) for i in (0,2))
    after=-1;seen=[]
    while True:
        page=records.read_page(path,path_filter=['x',0],after_sequence=after,limit=limit,max_bytes=budget)
        assert page['total']==6 and page['payload_bytes']<=budget
        assert page['records']
        seen.extend(page['records'])
        if page['next_sequence'] is None:break
        assert page['next_sequence']>after
        after=page['next_sequence']
    assert seen==rows[::2]
