"""独立只读观察的实时身份、追加游标与完整正文。"""
import base64
import json
import pytest
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA,ThreadStore


def test_observer_live_tail_complete_prefix_and_fork_identity(tmp_path):
    from society0.kernel.observation import Observation
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);observer=Observation(store.path)
        thread=threads.open('a',{'time':1},'decision')
        first=observer.list_threads(limit=1)
        assert first['total']==1 and first['items'][0]['id']==thread
        tail=observer.thread_tail(thread)
        assert tail['total']==1 and tail['complete_through']==0
        cursor=tail['cursor']
        assert observer.thread_tail(thread,cursor=cursor)['items']==[]
        threads.append_message(thread,{'role':'user','content':'原文🙂'*20000})
        page=observer.thread_tail(thread,cursor=cursor,max_bytes=1024)
        assert page['items'][0]['payload_ref']['seq']==2
        assert len(json.dumps(page,ensure_ascii=False,separators=(',',':')).encode())<=1024
        ref=page['items'][0]['payload_ref'];raw=bytearray();offset=0
        while True:
            part=observer.read_thread_payload(ref,offset=offset,size=777,max_bytes=2048)
            raw.extend(base64.b64decode(part['data']))
            if part['next_offset'] is None:break
            offset=part['next_offset']
        assert json.loads(raw)=={'role':'user','content':'原文🙂'*20000}
        store.complete(1)
        assert observer.thread_tail(thread,cursor=cursor)['complete_through']==2
        other=threads.open('b',2,'decision')
        found=observer.list_threads(cursor=first['cursor'])
        assert [item['id'] for item in found['items']]==[other]
        assert observer.status()['complete']['step']==1
    with StageStore.restore(tmp_path/'run',tmp_path/'restored',step=1) as store:
        observer=Observation(store.path)
        assert observer.thread_tail(thread)['complete_through']==2
        ThreadStore(store).append_message(thread,{'role':'user','content':'fork new'})
        assert observer.thread_tail(thread)['complete_through']==2
        with pytest.raises(ValueError,match='cursor'):observer.thread_tail(thread,cursor=cursor)
        store.complete(2)
        assert observer.thread_tail(thread)['complete_through']==3


def test_status_descriptor_publication_before_current_confirmation(tmp_path):
    from society0.kernel.observation import Observation
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        ThreadStore(store).open('a',0,'decision');store.complete(1)
        store._connection.execute('UPDATE _stage_runtime SET complete_step=0')
        status=Observation(store.path).status()
        assert status['complete_step_lower_bound']==0 and status['complete']['step']==1
        marker=store.path/'steps'/'00000000000000000001.json'
        data=json.loads(marker.read_text());data['run_id']='wrong';marker.write_text(json.dumps(data))
        with pytest.raises(ValueError,match='identity'):Observation(store.path).status()


def test_thread_complete_prefix_native_seek_cost_is_history_independent(tmp_path):
    from society0.kernel.observation import Observation
    counts=[]
    for history in (100,10000):
        with StageStore.create(tmp_path/str(history),THREAD_SCHEMA) as store:
            threads=ThreadStore(store);tid=threads.open('a',0,'decision')
            # 仅扩大合法事件索引的历史，正文不参与本查询。
            store.transaction(lambda w:w.executemany('INSERT INTO thread_events VALUES(?,?,?,?,?)',
                ((tid,seq,'message',0,1+(seq-1)//10) for seq in range(2,history+1))))
            store.transaction(lambda w:w.execute('UPDATE thread_heads SET last_seq=? WHERE id=?',(history,tid)))
            store.complete(1)
            with Observation(store.path) as observer:
                first=observer.thread_tail(tid,limit=1)
                cursor=first['cursor'];cursor['after']=history
                measured=[0]
                def count():measured[0]+=1;return False
                observer.reader._connection.set_progress_handler(count,1)
                last=observer.thread_tail(tid,cursor=cursor)
                assert last['items']==[]
                assert last['complete_through']==10
                counts.append(measured[0])
    assert counts[1]<counts[0]*2,counts


def test_separate_producer_observer_tracks_pending_failure_without_model_import(tmp_path):
    import subprocess,sys,os
    from society0.kernel.observation import Observation
    script='''import json,sys
from society0.kernel.storage import StageStore
from society0.kernel.threads import ThreadStore,THREAD_SCHEMA
with StageStore.create(sys.argv[1],THREAD_SCHEMA) as store:
 t=ThreadStore(store);i=t.open('a',0,'decision');store.complete(1)
 print(json.dumps({'thread':i,'heavy_imported':'society0.kernel.models' in sys.modules}),flush=True)
 input();t.append_message(i,{'role':'user','content':'pending original'})
 store.abort_step();print('failed',flush=True);input()
'''
    process=subprocess.Popen([sys.executable,'-c',script,str(tmp_path/'producer')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,env=os.environ.copy())
    try:
        started=json.loads(process.stdout.readline());assert not started['heavy_imported']
        with Observation(tmp_path/'producer') as observer:
            initial=observer.thread_tail(started['thread'])
            process.stdin.write('continue\n');process.stdin.flush()
            assert process.stdout.readline().strip()=='failed'
            tail=observer.thread_tail(started['thread'],cursor=initial['cursor'])
            assert tail['items'][0]['payload']['content']=='pending original'
            assert tail['complete_through']==1 and tail['total']==2
            assert observer.status()['failed'] is True
    finally:
        process.stdin.write('exit\n');process.stdin.flush()
        assert process.wait(timeout=10)==0


def test_complete_preparation_is_explicit_fixed_and_status_remains_live(tmp_path):
    import time
    from society0.kernel.observation import ObservationService
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision');store.complete(1)
        with ObservationService(store.path,cache_dir=tmp_path/'cache') as service:
            attempt=service.call('prepare_complete',{'step':1})
            assert attempt['state']=='preparing'
            threads.append_message(tid,{'role':'user','content':'new live'})
            deadline=time.monotonic()+15
            while True:
                progress=service.call('preparation_status',{})
                assert service.call('status',{})['complete']['step']==1
                if progress['state']!='preparing':break
                assert time.monotonic()<deadline
                time.sleep(.01)
            assert progress['state']=='ready',progress
            view=progress['view']
            assert service.call('thread_tail',{'thread_id':tid,'view':view})['total']==1
            assert service.call('thread_tail',{'thread_id':tid})['total']==2
            service.call('clear_prepared',{})
            with pytest.raises(ValueError,match='view_expired'):service.call('thread_tail',{'thread_id':tid,'view':view})
            service.prepare_complete(1)
            while service.preparation_status()['state']=='preparing':
                assert time.monotonic()<deadline;time.sleep(.01)
            assert service.preparation_status()['view']==view
            assert service.call('thread_tail',{'thread_id':tid,'view':view})['total']==1
        assert not list((tmp_path/'cache').glob('view-*'))


def _blocked_preparation(source,destination,step,run_id,channel):
    import time
    from pathlib import Path
    path=Path(destination);path.mkdir();(path/'partial').write_text('temporary')
    time.sleep(30)


def test_prepare_busy_killed_process_keeps_ready_and_clear_reclaims_owned_files(tmp_path,monkeypatch):
    import time
    import society0.kernel.observation as module
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        tid=ThreadStore(store).open('a',0,'decision');store.complete(1)
        with module.ObservationService(store.path,cache_dir=tmp_path/'cache') as service:
            service.prepare_complete(1)
            deadline=time.monotonic()+10
            while service.preparation_status()['state']=='preparing':
                assert time.monotonic()<deadline;time.sleep(.005)
            ready=service.preparation_status()['view']
            monkeypatch.setattr(module,'_prepare_worker',_blocked_preparation)
            service.prepare_complete(1)
            with pytest.raises(ValueError,match='busy'):service.prepare_complete(1)
            assert service.call('thread_tail',{'thread_id':tid,'view':ready})['total']==1
            process=service._pending[0];process.kill();process.join()
            assert service.preparation_status()['state']=='failed'
            assert service.call('thread_tail',{'thread_id':tid,'view':ready})['total']==1
            service.prepare_complete(1)
            service.clear_prepared()
            assert not list((tmp_path/'cache').iterdir())


def test_explicit_readonly_information_pages_expire_on_same_table_writes(tmp_path):
    from society0.kernel.observation import ObservationService,encoded
    from society0.kernel.interaction import Information
    from society0.kernel.information_sql import SQLInformation,DatasetSpec,DocumentSpec
    schema=['CREATE TABLE records(id INTEGER PRIMARY KEY,owner TEXT NOT NULL,body BLOB NOT NULL)',
            'CREATE INDEX records_owner ON records(owner,id)']
    body=('二进制原文🙂\n'+chr(0)).encode()*1000+b'\xff'
    with StageStore.create(tmp_path/'run',schema,initialize=lambda w:w.executemany('INSERT INTO records VALUES(?,?,?)',[(1,'a',body),(2,'a',b'next'),(3,'b',b'other')])) as store:
        def factory(reader):
            info=Information(lambda *a:True)
            only=lambda scope:('owner=?',(scope.actor,))
            info.mount('/data',SQLInformation('data',reader,{'items':DatasetSpec('records','id',('id','owner'),authorize=only),'content':DocumentSpec('records','id','body',authorize=only)}))
            return info
        with ObservationService(store.path,information_factory=factory) as service:
            params={'actor':'a','moment':{'time':1,'phase':'read'},'path':'/data/items','query':{'limit':1}}
            expired=0
            for _ in range(20):
                first=service.call('query',params)
                store.transaction(lambda w:w.execute("UPDATE records SET body=body||'x' WHERE id=3"))
                with pytest.raises(ValueError,match='cursor'):
                    service.call('query',{**params,'query':{'limit':1,'cursor':first['next_cursor']}})
                expired+=1
            assert expired==20
            first=service.call('query',params)
            second=service.call('query',{**params,'query':{'limit':1,'cursor':first['next_cursor']}})
            assert first['total']==second['total']==2 and second['next_cursor'] is None
            raw=bytearray();offset=0
            while True:
                part=service.call('read_document',{'actor':'a','moment':params['moment'],'path':'/data/content/1','offset':offset,'size':911,'max_bytes':2048})
                assert len(encoded(part))<=2048
                raw.extend(base64.b64decode(part['data']))
                if part['next_offset'] is None:break
                offset=part['next_offset']
            assert raw==body


def test_cli_status_uses_same_result_and_no_plugin_start(tmp_path):
    import subprocess,sys,os
    from society0.kernel.observation import Observation
    with StageStore.create(tmp_path/'run',[]):pass
    output=subprocess.check_output([sys.executable,'-m','society0.kernel.observation',str(tmp_path/'run')],env=os.environ.copy())
    assert json.loads(output)==Observation(tmp_path/'run').status()


def test_shared_resource_body_and_binary_artifact_are_bounded_and_complete(tmp_path):
    from society0.kernel.observation import ObservationService,encoded
    from society0.kernel.models import RESOURCE_SCHEMA,ResourceCalls
    payload={'response':{'vectors':[[index/17 for index in range(15000)]],'original':'汉字\n\"🙂'*10000}}
    with StageStore.create(tmp_path/'run',(*THREAD_SCHEMA,*RESOURCE_SCHEMA)) as store:
        calls=ResourceCalls(store);call=calls.begin('embedding','endpoint','model',{'input':['原文']})
        calls.event(call,'response',payload)
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        binary=bytes(range(256))*100
        artifact=store.prepare_artifact([binary]);threads.register_artifact(tid,'binary',artifact,actor='a')
        with ObservationService(store.path) as service:
            page=service.call('resource_tail',{'call_id':call,'max_bytes':1024})
            if page['items'][-1].get('payload_ref') is None:
                page=service.call('resource_tail',{'call_id':call,'cursor':page['cursor'],'max_bytes':1024})
            ref=page['items'][-1]['payload_ref'];raw=bytearray();offset=0
            while True:
                part=service.call('read_resource_payload',{'reference':ref,'offset':offset,'size':4111,'max_bytes':4096})
                assert len(encoded(part))<=4096
                raw.extend(base64.b64decode(part['data']))
                if part['next_offset'] is None:break
                offset=part['next_offset']
            assert json.loads(raw)==payload
            part=service.call('read_thread_artifact',{'thread_id':tid,'reference':'binary','actor':'a','offset':123,'size':1000,'max_bytes':2048})
            assert base64.b64decode(part['data'])==binary[123:1123]
            with pytest.raises(PermissionError):service.call('read_thread_artifact',{'thread_id':tid,'reference':'binary','actor':'b'})


def test_cli_historical_one_read_and_lifecycle_error_are_concrete(tmp_path):
    import subprocess,sys,os
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision');store.complete(1)
        threads.append_message(tid,{'role':'user','content':'pending'})
        command=[sys.executable,'-m','society0.kernel.observation',str(store.path)]
        invalid=subprocess.run([*command,'--request',json.dumps({'method':'prepare_complete','params':{'step':1}})],capture_output=True,env=os.environ.copy())
        assert invalid.returncode!=0
        failure=json.loads(invalid.stdout)
        assert failure['error']['code']=='session_required' and '--serve' in failure['error']['message']
        assert b'Traceback' not in invalid.stderr
        result=subprocess.check_output([*command,'--complete-step','1','--request',json.dumps({'method':'thread_tail','params':{'thread_id':tid}})],env=os.environ.copy())
        assert json.loads(result)['total']==1
        invalid=subprocess.run([*command,'--request',json.dumps({'method':'missing'})],capture_output=True,env=os.environ.copy())
        assert invalid.returncode!=0 and json.loads(invalid.stdout)['error']['code']=='unknown_method'
        assert b'Traceback' not in invalid.stderr


def test_cli_rebuilt_same_complete_identity_continues_cursor_and_large_reference(tmp_path):
    import subprocess,sys,os
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        original={'role':'user','content':'完整🙂'*2000}
        threads.append_message(tid,original);threads.open('b',0,'decision');store.complete(1);store.complete(2)
    command=[sys.executable,'-m','society0.kernel.observation',str(tmp_path/'run')]
    def call(method,params,step=1,success=True):
        run=subprocess.run([*command,'--complete-step',str(step),'--request',json.dumps({'method':method,'params':params})],capture_output=True,env=os.environ.copy())
        assert (run.returncode==0)==success,run.stderr.decode()
        return json.loads(run.stdout)
    first=call('list_threads',{'limit':1})
    second=call('list_threads',{'cursor':first['cursor'],'limit':1})
    assert second['items'][0]['actor']=='b'
    assert call('list_threads',{'cursor':first['cursor']},step=2,success=False)['error']['code']=='cursor_invalid'
    tail=call('thread_tail',{'thread_id':tid,'max_bytes':1024})
    reference=next(item['payload_ref'] for item in tail['items'] if 'payload_ref' in item)
    raw=bytearray();offset=0
    while True:
        part=call('read_thread_payload',{'reference':reference,'offset':offset,'size':8192,'max_bytes':16384})
        raw.extend(base64.b64decode(part['data']))
        if part['next_offset'] is None:break
        offset=part['next_offset']
    assert json.loads(raw)==original


@pytest.mark.asyncio
async def test_result_reference_discovery_and_original_row_ranges(tmp_path):
    from society0.kernel.results import RESULTS_SCHEMA,Results,StepResult
    from society0.kernel.observation import ObservationService,encoded
    original={'text':'原始结果\n\"🙂'*1000,'number':7}
    with StageStore.create(tmp_path/'run',RESULTS_SCHEMA) as store:
        await Results(store).write_phase(1,0,'measure',StepResult(metrics={'n':1},tables={'original':iter([original])}))
        store.complete(1)
        with ObservationService(store.path) as service:
            phases=service.call('result_phases',{'step':1})
            header=service.call('result_page',{'reference':phases['items'][0]['reference']})['items'][0]
            page=service.call('result_page',{'reference':header['tables']['original'],'max_bytes':512})
            assert len(encoded(page))<=512
            reference=page['items'][0];raw=bytearray();offset=0
            while True:
                part=service.call('read_result_record',{'reference':reference,'offset':offset,'size':2111,'max_bytes':2048})
                assert len(encoded(part))<=2048
                raw.extend(base64.b64decode(part['data']))
                if part['next_offset'] is None:break
                offset=part['next_offset']
            assert json.loads(raw)==original
            assert service.call('result_summary',{})['row_count']==1
