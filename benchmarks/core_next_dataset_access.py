"""独立只读进程顺序继续分页与随机范围；保留 OS 缓存。"""
import argparse
import json
import platform
import random
import resource
import time
from pathlib import Path
from society0.kernel.storage import StageReader
from society0.kernel.datasets import Datasets


def probe(source,*,pages=1000,limit=100,random_reads=1000):
    wall=time.perf_counter();cpu=time.process_time()
    with StageReader(source) as reader:
        identifier,artifact,count=reader.read(lambda r:r.query('SELECT id,artifact,count FROM datasets'))[0]
        ref={'kind':'dataset','id':identifier,'artifact':artifact};data=Datasets(reader)
        cursor=None;records=0;completed=0;last=-1;started=time.perf_counter();returned_bytes=0
        for _ in range(pages):
            page=data.page(ref,cursor=cursor,limit=limit,max_bytes=65536)
            assert page['total']==count
            for item in page['items']:
                assert item['ordinal']==last+1
                last=item['ordinal'];records+=1
            returned_bytes+=len(json.dumps(page,ensure_ascii=False,separators=(',',':')).encode())
            completed+=1;cursor=page['next_cursor']
            if cursor is None:break
        sequential=time.perf_counter()-started;rng=random.Random(31415);started=time.perf_counter()
        checked=0;raw_bytes=0
        for _ in range(random_reads):
            ordinal=rng.randrange(count)
            first=data.read_payload(ref,ordinal,offset=0,size=1)
            total=first['total_bytes'];offset=rng.randrange(max(total,1))
            part=data.read_payload(ref,ordinal,offset=offset,size=64)
            assert len(part['data'])==min(64,total-offset)
            # 多读相邻窗口验证偏移拼接，始终有界，不物化整条。
            adjacent=data.read_payload(ref,ordinal,offset=max(0,offset-7),size=78)
            assert part['data']==adjacent['data'][min(7,offset):min(7,offset)+len(part['data'])]
            checked+=1;raw_bytes+=len(part['data'])
        random_seconds=time.perf_counter()-started
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {'platform':platform.platform(),'python':platform.python_version(),'total_records':count,
            'sequential_pages':completed,'sequential_records':records,'last_ordinal':last,
            'sequential_seconds':sequential,'sequential_response_bytes':returned_bytes,
            'random_reads':checked,'random_payload_requests':checked*3,'random_returned_bytes':raw_bytes,
            'random_seconds':random_seconds,'sample_ranges_equal':True,
            'peak_rss_bytes':peak if platform.system()=='Darwin' else peak*1024,
            'wall_seconds':time.perf_counter()-wall,'cpu_seconds':time.process_time()-cpu,
            'limits':'OS cache retained; random sample uses three bounded calls per item including offset validation'}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source',type=Path);args=parser.parse_args()
    print(json.dumps(probe(args.source)))
