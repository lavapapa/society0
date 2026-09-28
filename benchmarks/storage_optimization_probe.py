"""固定c2ad77b合成负载，独立测量records writer与读取。"""
import argparse
import cProfile
import importlib.util
import json
import pstats
import random
import resource
import sqlite3
import subprocess
import tempfile
import time
import sys
import inspect
import statistics
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--module')
parser.add_argument('--revision', default='working-tree')
parser.add_argument('--count', type=int, default=204882)
parser.add_argument('--profile')
parser.add_argument('--directory')
parser.add_argument('--page-size',type=int)
parser.add_argument('--compression-level',type=int)
parser.add_argument('--large-bytes',type=int,default=2925528)
parser.add_argument('--uniform-value-bytes',type=int,default=0)
parser.add_argument('--stream-input',action='store_true')
parser.add_argument('--small-values',action='store_true')
args = parser.parse_args()
with tempfile.TemporaryDirectory(dir=args.directory) as directory:
    root = Path(directory)
    if args.module:
        spec = importlib.util.spec_from_file_location('benchmark_records', args.module)
        records = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(records)
    elif args.revision == 'working-tree':
        from society0 import checkpoint_records as records
    else:
        source = root/'records.py'
        source.write_bytes(subprocess.check_output(['git','show',args.revision+':src/society0/checkpoint_records.py']))
        spec = importlib.util.spec_from_file_location('baseline_records', source)
        records = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(records)
    if args.page_size:
        records.PAGE_SIZE=args.page_size
        records.SMALL_PAGE_SIZE=args.page_size
    if args.compression_level: records.COMPRESSION_LEVEL=args.compression_level
    rng = random.Random(17)
    uniform = 'z' * args.uniform_value_bytes
    entries = ({'sequence': i, 'path': ['environment','state','entries',str(i)], 'operation':'set',
        'value': uniform if args.uniform_value_bytes else i if args.small_values else 'z'*args.large_bytes if i==args.count-1 and args.large_bytes else {'firm_id':f'firm-{i%350}', 'day':i//350,
        'quantity':(i*7)%123456,'memo':'经营观察'*21+rng.randbytes(128).hex()}} for i in range(args.count))
    if not args.stream_input:
        entries = tuple(entries)
    path=root/'records.sqlite'
    before=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
    profile=cProfile.Profile() if args.profile else None
    if profile:profile.enable()
    start=time.perf_counter()
    records.write_records(path, entries, **({'record_count': args.count} if 'record_count' in inspect.signature(records.write_records).parameters else {}))
    seconds=time.perf_counter()-start
    if profile:
        profile.disable()
        with open(args.profile,'w') as handle:
            pstats.Stats(profile,stream=handle).strip_dirs().sort_stats('cumulative').print_stats(35)
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
    start=time.perf_counter()
    page=records.read_page(path,after_sequence=args.count-3,limit=2,max_bytes=1024)
    page_seconds=time.perf_counter()-start
    start=time.perf_counter()
    tail=b''.join(records.iter_record_bytes(path,args.count-1,offset=max(0,(args.uniform_value_bytes or args.large_bytes)-28) if not args.small_values else 0,max_bytes=50))
    tail_seconds=time.perf_counter()-start
    range_samples=[]
    for i in range(100):
        started=time.perf_counter()
        sample=b''.join(records.iter_record_bytes(path,(i*104729)%args.count,offset=0,max_bytes=64))
        assert sample.startswith(b'{')
        range_samples.append(time.perf_counter()-started)
    with sqlite3.connect(path) as db:
        stats=dict(db.execute('SELECT name,sum(pgsize) FROM dbstat GROUP BY name'))
        raw_bytes=db.execute('SELECT raw_bytes FROM totals').fetchone()[0]
        page_size=db.execute('PRAGMA page_size').fetchone()[0]
    print(json.dumps({'revision':args.revision,'page_size':page_size,'compression_level':getattr(records,'COMPRESSION_LEVEL',6),'records':args.count,'raw_bytes':raw_bytes,'rss_unit':'bytes','write_seconds':seconds,
        'rss_before':before,'rss_peak':peak,'rss_extra':peak-before,'file_bytes':path.stat().st_size,
        'table_bytes':stats,'page_seconds':page_seconds,'random_range_median_seconds':statistics.median(range_samples),'random_range_p95_seconds':sorted(range_samples)[94],'range_seconds':tail_seconds,'range_bytes':len(tail)},indent=2))
