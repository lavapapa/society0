"""索引表示与分页复杂度的可重复验收。"""
import pytest

from society0.incremental_checkpoint import V4CheckpointStore, SealedTickDelta
from society0.observation import ObservationReader


def test_index_metadata_does_not_repeat_paths_and_ancestor_versions(tmp_path):
    store = V4CheckpointStore(tmp_path)
    store.publish_root(({'sequence':i,'path':['environment','state','entries',str(i)],'operation':'set','value':i} for i in range(4000)))
    with ObservationReader(tmp_path) as reader:
        reader.sync()
        size = reader.db.execute('PRAGMA page_count').fetchone()[0] * reader.db.execute('PRAGMA page_size').fetchone()[0]
        assert size < 4000 * 400


@pytest.mark.parametrize("limit", [1,1000000])
def test_page_total_does_not_scan_current_scope(tmp_path,limit):
    counts=[]
    for n in (100,10000):
        root=tmp_path/str(n);root.mkdir()
        store=V4CheckpointStore(root)
        store.publish_root(({'sequence':i,'path':['rows',str(i)],'operation':'set','value':i} for i in range(n)))
        with ObservationReader(root) as reader:
            reader.sync()
            instructions=0
            def progress():
                nonlocal instructions
                instructions+=1
                return 0
            reader.db.set_progress_handler(progress,1)
            page=reader.state_page(path=['rows'],limit=limit,max_bytes=512)
            reader.db.set_progress_handler(None,0)
            assert page['total']==n
            counts.append(instructions)
    assert counts[1] < counts[0]*3, counts


def test_historical_page_seeks_version_without_scanning_hot_key_history(tmp_path):
    counts=[]
    for history in (10,3000):
        root=tmp_path/str(history);root.mkdir()
        store=V4CheckpointStore(root)
        first=store.publish_root([{'sequence':0,'path':['hot'],'operation':'set','value':1}])
        with ObservationReader(root) as reader:
            reader.sync()
            with reader.db:
                for i in range(1,history+1):
                    reader.db.execute('INSERT INTO entries SELECT path_id,?,0,source,sequence,raw_bytes,ordinal,structural FROM entries WHERE start=1',(-i,))
                    entry=reader.db.execute('SELECT last_insert_rowid()').fetchone()[0]
                    reader.db.execute('INSERT INTO scopes VALUES (0,0,?,0,?)',(-i,entry))
            store.publish(SealedTickDelta(1,({'sequence':0,'path':['hot'],'operation':'set','value':2},),()))
            reader.sync()
            instructions=0
            def progress():
                nonlocal instructions
                instructions+=1
                return 0
            reader.db.set_progress_handler(progress,1)
            page=reader.state_page(first['checkpoint_id'],limit=1)
            reader.db.set_progress_handler(None,0)
            assert page['total']==1 and page['items'][0]['value']==1
            counts.append(instructions)
    assert counts[1] < counts[0]*3, counts


def test_old_index_requires_explicit_rebuild(tmp_path):
    import sqlite3
    import pytest
    index=tmp_path/'index';index.mkdir()
    with sqlite3.connect(index/'observation.sqlite') as db:
        db.execute('CREATE TABLE entries(path_key TEXT)')
    with pytest.raises(ValueError,match='index_format_mismatch.*rebuild-index'):
        ObservationReader(tmp_path,index_dir=index)
    with ObservationReader(tmp_path,index_dir=index,rebuild=True) as reader:
        assert reader.db.execute('PRAGMA user_version').fetchone()[0]==3


def test_deleted_keys_require_bounded_explicit_preparation(tmp_path):
    import pytest
    store=V4CheckpointStore(tmp_path)
    store.publish_root(({'sequence':i,'path':['rows',str(i)],'operation':'set','value':i} for i in range(2000)))
    old=store.publish(SealedTickDelta(1,tuple({'sequence':i,'path':['rows',str(i)],'operation':'delete'} for i in range(1999)),()))
    store.publish(SealedTickDelta(2,({'sequence':0,'path':['later'],'operation':'set','value':1},),()))
    with ObservationReader(tmp_path) as reader:
        reader.sync()
        with pytest.raises(ValueError,match='index_pending.*prepare_state'):
            reader.state_page(old['checkpoint_id'],path=['rows'],limit=1)
        prepared=reader.prepare_state(old['checkpoint_id'],path=['rows'])
        assert prepared['state']=='ready' and prepared['total']==1
        page=reader.state_page(old['checkpoint_id'],path=['rows'],limit=1)
        assert page['items'][0]['value']==1999 and page['total']==1
        assert reader.prepared_state()['checkpoint_id']==old['checkpoint_id']
        reader.clear_prepared_state()
        assert reader.prepared_state()['state']=='absent'


def test_preparation_crash_keeps_old_projection_and_index_watermark(tmp_path):
    import json
    import subprocess
    import sys
    import time
    store=V4CheckpointStore(tmp_path)
    marker=store.publish_root(({'sequence':i,'path':['rows',str(i)],'operation':'set','value':i} for i in range(1200)))
    index=tmp_path/'index'
    with ObservationReader(tmp_path,index_dir=index) as reader:
        reader.sync()
        reader.prepare_state(marker['checkpoint_id'],['rows','0'])
    script='''
import sys,time
from pathlib import Path
from society0.observation import ObservationReader
root=Path(sys.argv[1])
with ObservationReader(root,index_dir=root/'index') as reader:
    def progress(value):
        (root/'preparing').write_text('ready')
        time.sleep(30)
    reader._prepare_progress=progress
    reader.prepare_state(sys.argv[2],['rows'])
'''
    process=subprocess.Popen([sys.executable,'-c',script,str(tmp_path),marker['checkpoint_id']])
    try:
        deadline=time.monotonic()+5
        while not (tmp_path/'preparing').exists() and time.monotonic()<deadline:
            time.sleep(.01)
        assert (tmp_path/'preparing').exists()
        process.kill();process.wait(5)
        with ObservationReader(tmp_path,index_dir=index) as reader:
            assert reader.prepared_state()['path']==['rows','0']
            assert reader.db.execute('SELECT count(*) FROM prepared_rows').fetchone()[0]==1
            assert reader.status()['indexed_checkpoint']['checkpoint_id']==marker['checkpoint_id']
    finally:
        if process.poll() is None:
            process.kill();process.wait(5)


def test_http_preparation_is_single_slot_and_survives_disconnect(tmp_path):
    import json
    import socket
    import subprocess
    import sys
    import time
    import urllib.request
    import urllib.error
    store=V4CheckpointStore(tmp_path)
    marker=store.publish_root([{'sequence':0,'path':['a'],'operation':'set','value':1}])
    with ObservationReader(tmp_path,index_dir=tmp_path/'index') as reader:
        reader.sync()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    script='''
import time
from society0.observation import ObservationReader,main
original=ObservationReader.prepare_state
def slow(self,*a,**kw):
    time.sleep(.8)
    return original(self,*a,**kw)
ObservationReader.prepare_state=slow
main()
'''
    process=subprocess.Popen([sys.executable,'-c',script,str(tmp_path),'--index-dir',str(tmp_path/'index'),'--serve',str(port)],stderr=subprocess.DEVNULL)
    def request(method,params=None):
        req=urllib.request.Request(f'http://127.0.0.1:{port}',data=json.dumps({'method':method,'params':params or {}}).encode())
        try:
            with urllib.request.urlopen(req,timeout=1) as response:
                return json.load(response)
        except urllib.error.HTTPError as response:
            return json.load(response)
    try:
        deadline=time.monotonic()+5
        while True:
            try:
                request('status');break
            except OSError:
                assert time.monotonic()<deadline
                time.sleep(.02)
        params={'checkpoint_id':marker['checkpoint_id'],'path':[]}
        assert request('prepare_state',params)['result']['state']=='queued'
        assert request('prepare_state',params)['error']=='preparation_busy'
        start=time.monotonic();request('status');assert time.monotonic()-start<.4
        while request('prepared_state')['result'].get('request',{}).get('state')!='ready':
            assert time.monotonic()<deadline
            time.sleep(.03)
        body=json.dumps({'method':'prepare_state','params':params}).encode()
        with socket.create_connection(('127.0.0.1',port)) as sock:
            sock.sendall(b'POST / HTTP/1.0\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
        time.sleep(.1)
        deadline=time.monotonic()+5
        while request('prepared_state')['result'].get('request',{}).get('state')!='ready':
            assert time.monotonic()<deadline
            time.sleep(.03)
        assert request('state_page')['result']['items'][0]['value']==1
    finally:
        process.terminate();process.wait(5)


def test_current_page_and_delta_seek_only_active_memberships(tmp_path):
    costs=[]
    for history in (10,300):
        root=tmp_path/str(history);root.mkdir()
        store=V4CheckpointStore(root)
        store.publish_root([{'sequence':0,'path':['hot'],'operation':'set','value':0}])
        with ObservationReader(root) as reader:
            reader.sync()
            for step in range(1,history+1):
                store.publish(SealedTickDelta(step,({'sequence':0,'path':['hot'],'operation':'set','value':step},),()))
                reader.sync()
            instructions=0
            def progress():
                nonlocal instructions
                instructions+=1
                return 0
            reader.db.set_progress_handler(progress,1)
            assert reader.state_page(limit=1)['items'][0]['value']==history
            reader.db.set_progress_handler(None,0)
            page_cost=instructions
            store.publish(SealedTickDelta(history+1,({'sequence':0,'path':['hot'],'operation':'set','value':history+1},),()))
            instructions=0;reader.db.set_progress_handler(progress,1)
            reader.sync();reader.db.set_progress_handler(None,0)
            costs.append((page_cost,instructions))
    assert costs[1][0] < costs[0][0]*3, costs
    assert costs[1][1] < costs[0][1]*2, costs


def test_cursor_identity_distinguishes_boolean_and_integer_path_parts(tmp_path):
    store=V4CheckpointStore(tmp_path)
    store.publish_root([{'sequence':i,'path':path,'operation':'set','value':i} for i,path in enumerate([[True,'a'],[True,'b'],[1,'a'],[1,'b']])],metadata={})
    with ObservationReader(tmp_path) as reader:
        reader.sync()
        first=reader.state_page(path=[True],limit=1)
        with pytest.raises(ValueError,match='cursor_mismatch'):
            reader.state_page(path=[1],cursor=first['next_cursor'])


@pytest.mark.parametrize('kind',['checkpoint','dataset'])
def test_content_reference_sequence_preserves_integer_type(tmp_path,kind):
    from society0.result_datasets import write_dataset
    store=V4CheckpointStore(tmp_path)
    store.publish_root([{'sequence':i,'path':[str(i)],'operation':'set','value':i} for i in range(2)],metadata={})
    with ObservationReader(tmp_path) as reader:
        reader.sync()
        if kind=='checkpoint':
            ref=reader.state_page()['items'][1]['content_ref']
        else:
            dataset=write_dataset(tmp_path,[{'body':'x'*10000} for _ in range(2)],step=None,name='diagnostic')
            dataset['publication']='run_diagnostics'
            ref=reader.dataset_page(dataset,max_bytes=1024)['records'][1]['record_ref']
        ref={**ref,'sequence':True}
        with pytest.raises(ValueError,match='content_ref'):
            reader.read_content(ref,max_bytes=10)


def test_long_typed_keys_have_compact_reversible_index_and_exact_pages(tmp_path):
    keys = [f'actor:{i}:resource:原料:date:2026-09-29:' + ('delivery:contract:"签约"\\\n' * 35) for i in range(2000)]
    store = V4CheckpointStore(tmp_path)
    marker = store.publish_root({'sequence': i, 'path': ['rows', key], 'operation': 'set', 'value': i} for i, key in enumerate(keys))
    with ObservationReader(tmp_path) as reader:
        reader.sync()
        path_bytes = reader.db.execute("SELECT sum(pgsize) FROM dbstat WHERE name='paths' OR name LIKE '%paths%' OR name='parts' OR name LIKE '%parts%'").fetchone()[0]
        assert path_bytes < 1000000
        assert reader.state_page(path=['rows', keys[-1]])['items'][0]['path'] == ['rows', keys[-1]]
        store.publish(SealedTickDelta(1, ({'sequence': 0, 'path': ['rows', keys[-1]], 'operation': 'set', 'value': 'changed'},), ()))
        reader.sync()
        assert reader.state_page(marker['checkpoint_id'], path=['rows', keys[-1]])['items'][0]['value'] == 1999
        assert reader.state_page(path=['rows', keys[-1]])['items'][0]['value'] == 'changed'
        reader.prepare_state(marker['checkpoint_id'], path=['rows', keys[-1]])
        assert reader.state_page(marker['checkpoint_id'], path=['rows', keys[-1]])['total'] == 1


def test_direct_membership_storage_is_independent_of_path_depth(tmp_path):
    store = V4CheckpointStore(tmp_path)
    store.publish_root({'sequence': i, 'path': ['a', 'b', 'c', str(i)], 'operation': 'set', 'value': i} for i in range(1000))
    with ObservationReader(tmp_path) as reader:
        reader.sync()
        assert reader.db.execute('SELECT count(*) FROM scopes').fetchone()[0] == 1000
        for path in [[], ['a'], ['a', 'b'], ['a', 'b', 'c']]:
            page=reader.state_page(path=path, limit=4)
            assert page['total']==1000
            assert [item['value'] for item in page['items']]==[0,1,2,3]


def test_many_active_scopes_require_preparation_and_deleted_scopes_do_not_accumulate(tmp_path):
    import json
    store=V4CheckpointStore(tmp_path)
    first=store.publish_root({'sequence': i, 'path': ['rows', str(i), 'leaf'], 'operation': 'set', 'value': i} for i in range(300))
    with ObservationReader(tmp_path) as reader:
        reader.sync()
        with pytest.raises(ValueError,match='prepare_state'):
            reader.state_page(path=['rows'],limit=1)
        reader.prepare_state(first['checkpoint_id'],path=['rows'])
        assert reader.state_page(path=['rows'],limit=1)['items'][0]['value']==0
        store.publish(SealedTickDelta(1, tuple({'sequence':i,'path':['rows',str(i)],'operation':'delete'} for i in range(299)), ()))
        reader.sync()
        assert reader.state_page(path=['rows'],limit=1)['total']==1
        assert reader.state_page(path=['rows'],limit=1)['items'][0]['value']==299


def test_dataset_cursor_rejects_another_partition_of_same_container(tmp_path):
    from society0.result_datasets import externalize_history
    value={'first':{'by_tick':{'0':'a','1':'b'}},'second':{'by_tick':{'0':'c','1':'d'}}}
    externalize_history(tmp_path,value)
    with ObservationReader(tmp_path) as reader:
        first=reader.dataset_page(value['first']['by_tick'],limit=1)
        assert first['next_cursor']
        with pytest.raises(ValueError,match='cursor_mismatch'):
            reader.dataset_page(value['second']['by_tick'],cursor=first['next_cursor'])


def test_key_codec_change_requires_explicit_index_rebuild(tmp_path):
    index=tmp_path/'index'
    with ObservationReader(tmp_path,index_dir=index) as reader:
        with reader.db:
            reader.db.execute("UPDATE meta SET value='another-zlib' WHERE key='key_codec'")
    with pytest.raises(ValueError,match='index_format_mismatch.*rebuild'):
        ObservationReader(tmp_path,index_dir=index)
    with ObservationReader(tmp_path,index_dir=index,rebuild=True) as reader:
        assert reader.prepared_state()['state']=='absent'


def test_page_reuses_one_bounded_content_reader(tmp_path,monkeypatch):
    from society0 import checkpoint_records as records
    store=V4CheckpointStore(tmp_path)
    store.publish_root({'sequence':i,'path':['rows',str(i)],'operation':'set','value':i} for i in range(100))
    with ObservationReader(tmp_path) as reader:
        reader.sync()
        verified=[];require=reader._require_source
        monkeypatch.setattr(reader,'_require_source',lambda path:(verified.append(path),require(path))[1])
        opens=[];decode=[];original=records._open;decompress=records.gzip.decompress
        monkeypatch.setattr(records,'_open',lambda path:(opens.append(path),original(path))[1])
        monkeypatch.setattr(records.gzip,'decompress',lambda data:(decode.append(len(data)),decompress(data))[1])
        assert len(reader.state_page(path=['rows'])['items'])==100
        assert len(opens)==1
        assert len(decode)==1
        assert len(verified)==1


def test_http_slow_request_body_does_not_block_status(tmp_path):
    import json,socket,subprocess,sys,time,urllib.request
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    process=subprocess.Popen([sys.executable,'-m','society0.observation',str(tmp_path),'--index-dir',str(tmp_path/'index'),'--serve',str(port)],stderr=subprocess.DEVNULL)
    slow=None
    def status():
        req=urllib.request.Request(f'http://127.0.0.1:{port}',data=b'{"method":"status"}')
        with urllib.request.urlopen(req,timeout=.6) as response:return json.load(response)
    try:
        deadline=time.monotonic()+5
        while True:
            try:status();break
            except OSError:
                assert time.monotonic()<deadline;time.sleep(.02)
        slow=socket.create_connection(('127.0.0.1',port))
        slow.sendall(b'POST / HTTP/1.0\r\nContent-Length: 100\r\n\r\n{')
        time.sleep(.05)
        assert 'result' in status()
    finally:
        if slow:slow.close()
        process.terminate();process.wait(5)


def test_http_slow_response_reader_does_not_block_status(tmp_path):
    import json,socket,subprocess,sys,time,urllib.request
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    script='''from society0.observation import ObservationReader,main
ObservationReader.read_content=lambda self,**kw:{"data":b"x"*(8*1024*1024),"total_bytes":8*1024*1024,"next_offset":None}
main()
'''
    process=subprocess.Popen([sys.executable,'-c',script,str(tmp_path),'--index-dir',str(tmp_path/'index'),'--serve',str(port)],stderr=subprocess.DEVNULL)
    slow=None
    def status():
        req=urllib.request.Request(f'http://127.0.0.1:{port}',data=b'{"method":"status"}')
        with urllib.request.urlopen(req,timeout=.6) as response:return json.load(response)
    try:
        deadline=time.monotonic()+5
        while True:
            try:status();break
            except OSError:
                assert time.monotonic()<deadline;time.sleep(.02)
        slow=socket.socket();slow.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,1024);slow.connect(('127.0.0.1',port))
        body=b'{"method":"read_content","params":{"ref":{}}}'
        slow.sendall(b'POST / HTTP/1.0\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
        time.sleep(.15)
        assert 'result' in status()
    finally:
        if slow:slow.close()
        process.terminate();process.wait(5)
