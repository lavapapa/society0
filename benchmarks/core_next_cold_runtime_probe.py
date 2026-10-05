"""独立冷进程：固定页/范围读取、小投影修改与完整点恢复；不物化全批正文。"""
import argparse
import json
import platform
import resource
import time
from pathlib import Path
from society0.kernel.storage import StageStore
from society0.kernel.datasets import Datasets
from benchmarks.core_next_cold_datasets_probe import space


def probe(source,target,*,ordinal=4,steps=20):
    started=time.perf_counter();cpu=time.process_time()
    with StageStore.open(source) as store:
        opened=time.perf_counter()-started
        identifier,artifact,count=store.read(lambda r:r.query('SELECT id,artifact,count FROM datasets'))[0]
        ref={'kind':'dataset','id':identifier,'artifact':artifact}
        data=Datasets(store)
        before=time.perf_counter()
        for _ in range(1000):
            page=data.page(ref,limit=10,max_bytes=4096)
            assert page['total']==count
        pages=time.perf_counter()-before
        before=time.perf_counter()
        part=data.read_payload(ref,ordinal,offset=65530,size=64)
        ranged=time.perf_counter()-before
        assert len(part['data'])==64
        initial=store.read(lambda r:r.complete_step)
        timings=[]
        for step in range(initial+1,initial+1+steps):
            before=time.perf_counter()
            store.transaction(lambda writer:writer.execute('UPDATE datasets SET name=? WHERE id=?',(f'hot-{step}',identifier)))
            written=time.perf_counter()-before
            session=store._session.memory_used
            before=time.perf_counter();store.complete(step);completed=time.perf_counter()-before
            timings.append({'step':step,'write_seconds':written,'complete_seconds':completed,'session_bytes':session})
    before=time.perf_counter()
    with StageStore.restore(source,target) as restored:
        restore=time.perf_counter()-before
        assert Datasets(restored).read_payload(ref,ordinal,offset=65530,size=64)==part
        assert restored.read(lambda r:r.query('SELECT name FROM datasets'))==[(f'hot-{initial+steps}',)]
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {'platform':platform.platform(),'python':platform.python_version(),'records':count,
            'open_seconds':opened,'1000_pages_seconds':pages,'range_64_bytes_seconds':ranged,
            'range_record_bytes':part['total_bytes'],'steps':timings,'restore_seconds':restore,
            'source_space':space([source]),'restore_space':space([target]),'combined_space':space([source,target]),
            'peak_rss_bytes':peak if platform.system()=='Darwin' else peak*1024,
            'wall_seconds':time.perf_counter()-started,'cpu_seconds':time.process_time()-cpu,
            'limits':'fixed dataset-header projection update, not business migration; OS cache retained; full-record values verified separately'}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source',type=Path);parser.add_argument('target',type=Path)
    parser.add_argument('--ordinal',type=int,default=4);args=parser.parse_args()
    print(json.dumps(probe(args.source,args.target,ordinal=args.ordinal)))
