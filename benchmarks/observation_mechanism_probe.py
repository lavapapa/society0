"""同元数据读取器比较基线/当前观察索引机制，包含 WAL 峰值和历史分页。"""
import argparse
import cProfile
import importlib.util
import io
import json
from pathlib import Path
import pstats
import resource
import subprocess
import sys
import tempfile
import threading
import time
from society0.incremental_checkpoint import V4CheckpointStore, SealedTickDelta
from society0.observation import ObservationReader


def vm_work(reader, action):
    instructions=0
    def progress():
        nonlocal instructions
        instructions+=1
        return 0
    reader.db.set_progress_handler(progress,1)
    before=time.perf_counter()
    try:
        result=action()
        error=None
    except ValueError as exc:
        result=None;error=str(exc)
    finally:
        reader.db.set_progress_handler(None,0)
    return {'instructions':instructions,'seconds':time.perf_counter()-before,'error':error,'total':(result or {}).get('total')}


def history_probe(root, reader_cls):
    samples=[]
    for count in (10,300):
        run=root/str(count);run.mkdir()
        store=V4CheckpointStore(run)
        first=store.publish_root([{'sequence':0,'path':['hot'],'operation':'set','value':0}])
        with reader_cls(run) as reader:
            reader.sync()
            for step in range(1,count+1):
                store.publish(SealedTickDelta(step,({'sequence':0,'path':['hot'],'operation':'set','value':step},),()))
                reader.sync()
            store.publish(SealedTickDelta(count+1,({'sequence':0,'path':['hot'],'operation':'set','value':count+1},),()))
            sample={'history':count,'delta':vm_work(reader,reader.sync),
                    'old_page':vm_work(reader,lambda:reader.state_page(first['checkpoint_id'],limit=1)),
                    'current_page':vm_work(reader,lambda:reader.state_page(limit=1))}
            samples.append(sample)
    run=root/'deleted';run.mkdir();store=V4CheckpointStore(run)
    store.publish_root(({'sequence':i,'path':['rows',str(i)],'operation':'set','value':i} for i in range(5000)))
    old=store.publish(SealedTickDelta(1,tuple({'sequence':i,'path':['rows',str(i)],'operation':'delete'} for i in range(4999)),()))
    store.publish(SealedTickDelta(2,({'sequence':0,'path':['other'],'operation':'set','value':1},),()))
    with reader_cls(run) as reader:
        reader.sync()
        deleted={'plain':vm_work(reader,lambda:reader.state_page(old['checkpoint_id'],path=['rows'],limit=1))}
        if hasattr(reader,'prepare_state'):
            deleted['prepare']=vm_work(reader,lambda:reader.prepare_state(old['checkpoint_id'],path=['rows']))
            deleted['prepared_page']=vm_work(reader,lambda:reader.state_page(old['checkpoint_id'],path=['rows'],limit=1))
            deleted['prepared_table_bytes']=reader.db.execute("SELECT sum(pgsize) FROM dbstat WHERE name='prepared_rows'").fetchone()[0]
    return {'hot_history':samples,'deleted_keys':deleted}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline',action='store_true')
    parser.add_argument('--records',type=int,default=204882)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    reader_cls=ObservationReader
    with tempfile.TemporaryDirectory(prefix='society0-index-mechanism-') as directory:
        root=Path(directory)
        if args.baseline:
            code=root/'old_observation.py'
            code.write_bytes(subprocess.check_output(['git','show','c2ad77b:src/society0/observation.py']))
            spec=importlib.util.spec_from_file_location('society0._old_observation',code)
            module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
            reader_cls=module.ObservationReader
        store=V4CheckpointStore(root)
        marker=store.publish_root(({'sequence':i,'path':['environment','state','entries',str(i)],'operation':'set','value':'x'*550} for i in range(args.records)),metadata={'run_id':'same-query-workload'})
        index=root/'index';peaks={};stop=threading.Event()
        def sizes():
            return {p.name:p.stat().st_size for p in index.glob('*') if p.is_file()}
        def watch():
            while not stop.wait(.005):
                for name,size in sizes().items():peaks[name]=max(peaks.get(name,0),size)
        monitor=threading.Thread(target=watch);monitor.start()
        with reader_cls(root,index_dir=index) as reader:
            profile=cProfile.Profile();start=time.perf_counter();profile.enable();reader.sync();profile.disable()
            initial=time.perf_counter()-start
            increments=[]
            for step in range(1,21):
                store.publish(SealedTickDelta(step,({'sequence':0,'path':['environment','state','entries','0'],'operation':'set','value':step},),()))
                start=time.perf_counter();reader.sync();increments.append(time.perf_counter()-start)
            queries={}
            for name,checkpoint in [('current',None),('historical',marker['checkpoint_id'])]:
                samples=[]
                for _ in range(20):
                    start=time.perf_counter();page=reader.state_page(checkpoint,path=['environment','state','entries'],limit=100);samples.append(time.perf_counter()-start)
                    assert page['total']==args.records
                queries[name]={'seconds':samples,'p95_seconds':sorted(samples)[18]}
            data={'baseline':args.baseline,'records':args.records,'initial_index_profiled_seconds':initial,
                'dbstat':dict(reader.db.execute('SELECT name,sum(pgsize) FROM dbstat GROUP BY name')),
                'increments_seconds':increments,'queries':queries,'files_live':sizes(),
                'peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
            reader.db.execute('PRAGMA wal_checkpoint(PASSIVE)')
            data['files_after_passive']=sizes()
        stop.set();monitor.join();data['files_after_close']=sizes();data['files_peak']=peaks
        report=io.StringIO();pstats.Stats(profile,stream=report).sort_stats('cumtime').print_stats(20)
        data['profile']=report.getvalue();data['temporary_artifacts_removed']=True
        data['history_probe']=history_probe(root,reader_cls)
    Path(args.output).write_text(json.dumps(data,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
