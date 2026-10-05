"""实际组件顺序元数据读取：只统计有界解块，不接触正文。"""
import argparse,gzip,json,sqlite3,time,resource,sys
from society0 import checkpoint_records as records
p=argparse.ArgumentParser();p.add_argument('component');a=p.parse_args()
original=gzip.decompress;calls=raw=compressed=0

def decode(payload):
 global calls,raw,compressed
 result=original(payload);calls+=1;raw+=len(result);compressed+=len(payload);return result
records.gzip.decompress=decode
start=time.perf_counter();count=sum(1 for _ in records.iter_metadata(a.component));seconds=time.perf_counter()-start
with sqlite3.connect('file:'+a.component+'?mode=ro&immutable=1',uri=True) as db:
 tables=dict(db.execute('select name,sum(pgsize) from dbstat group by name'))
print(json.dumps(dict(records=count,seconds=seconds,decompression_calls=calls,decoded_bytes=raw,compressed_bytes=compressed,peak_rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024),table_bytes=tables)))
