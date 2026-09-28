"""相同实际正文的旧/新组件随机范围和页会话读取对照。"""
import argparse,json,statistics,time
from society0 import checkpoint_records as records
p=argparse.ArgumentParser();p.add_argument('components',nargs='+');a=p.parse_args()
results=[]
for component in a.components:
 samples=[]
 for i in range(100):
  sequence=(i*104729)%1448471
  started=time.perf_counter()
  with records.RecordReader(component) as reader:
   size=reader.raw_bytes(sequence)
   content=b''.join(reader.iter_bytes(sequence,offset=max(0,size-64),max_bytes=64))
   assert len(content)==min(size,64)
  samples.append(time.perf_counter()-started)
 started=time.perf_counter()
 with records.RecordReader(component) as reader:
  for sequence in range(100000,100100):
   size=reader.raw_bytes(sequence);content=b''.join(reader.iter_bytes(sequence,offset=max(0,size-64),max_bytes=64))
   assert len(content)==min(size,64)
  cached=reader.cached_bytes
 results.append(dict(component=component,random_median_seconds=statistics.median(samples),random_p95_seconds=sorted(samples)[94],sequential_page_seconds=time.perf_counter()-started,cache_bytes=cached))
print(json.dumps(results))
