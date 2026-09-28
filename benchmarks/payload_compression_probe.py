"""真实不可变正文块抽样压缩级别；源组件保持只读。"""
import argparse,gzip,json,sqlite3,time
p=argparse.ArgumentParser();p.add_argument('component');a=p.parse_args()
db=sqlite3.connect('file:'+a.component+'?mode=ro&immutable=1',uri=True)
results={str(level):{'bytes':0,'seconds':0} for level in (1,3,6,9)};raw_bytes=blocks=0
for i,(payload,) in enumerate(db.execute('select payload from chunks order by raw_start')):
 if i%13:continue
 raw=gzip.decompress(payload);raw_bytes+=len(raw);blocks+=1
 for level in (1,3,6,9):
  start=time.perf_counter();encoded=gzip.compress(raw,compresslevel=level,mtime=0)
  results[str(level)]['seconds']+=time.perf_counter()-start;results[str(level)]['bytes']+=len(encoded)
print(json.dumps(dict(sample_blocks=blocks,raw_bytes=raw_bytes,levels=results)))
