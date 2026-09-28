"""以现有索引顺序读取typed key，测量有界字典块的压缩与fence成本。"""
import argparse,gzip,json,sqlite3,struct,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('component');a=p.parse_args()
db=sqlite3.connect(Path(a.component).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
result={'keys':0,'blocks':0,'raw_key_bytes':0,'block_raw_bytes':0,'gzip3_bytes':0,'gzip6_bytes':0,'fence_utf8_bytes':0,'gzip3_seconds':0,'gzip6_seconds':0}
buffer=bytearray();prefix=None;first=None

def emit():
 if not buffer:return
 result['blocks']+=1;result['block_raw_bytes']+=len(buffer);result['fence_utf8_bytes']+=len(first)
 for level in (3,6):
  start=time.perf_counter();packed=gzip.compress(buffer,compresslevel=level,mtime=0)
  result[f'gzip{level}_seconds']+=time.perf_counter()-start;result[f'gzip{level}_bytes']+=len(packed)
 buffer.clear()
for prefix_id,leaf,count in db.execute('SELECT prefix_id,leaf,count(*) FROM records GROUP BY prefix_id,leaf ORDER BY prefix_id,leaf'):
 raw=leaf.encode();entry=struct.pack('<Q',count)+raw+b'\0'
 if prefix_id!=prefix or len(buffer)+len(entry)>65536:
  emit();first=raw;prefix=prefix_id
 if not buffer:first=raw
 buffer.extend(entry);result['keys']+=1;result['raw_key_bytes']+=len(raw)
emit();print(json.dumps(result,indent=2))
