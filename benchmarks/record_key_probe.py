"""只读样本：长键原文、独立压缩和共享字典压缩成本，不改源组件。"""
import argparse,json,sqlite3,zlib,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('component');p.add_argument('--stride',type=int,default=13);a=p.parse_args()
db=sqlite3.connect(Path(a.component).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
dictionaries={}
for prefix, in db.execute('SELECT id FROM prefixes'):
 values=[leaf.encode() for leaf, in db.execute('SELECT leaf FROM records WHERE prefix_id=? LIMIT 32',(prefix,))]
 dictionaries[prefix]=b'\n'.join(values)[-32768:]
result={'sample_count':0,'raw_utf8_bytes':0,'raw_deflate_bytes':0,'dictionary_deflate_bytes':0,'dictionary_bytes':sum(map(len,dictionaries.values())),'plain_seconds':0,'dictionary_seconds':0}
by_prefix={}
for prefix,leaf in db.execute('SELECT prefix_id,leaf FROM records WHERE sequence % ?=0',(a.stride,)):
 raw=leaf.encode();result['sample_count']+=1;result['raw_utf8_bytes']+=len(raw)
 start=time.perf_counter();c=zlib.compressobj(3,wbits=-15);plain=c.compress(raw)+c.flush();result['plain_seconds']+=time.perf_counter()-start
 start=time.perf_counter();c=zlib.compressobj(3,wbits=-15,zdict=dictionaries[prefix]);packed=c.compress(raw)+c.flush();result['dictionary_seconds']+=time.perf_counter()-start
 result['raw_deflate_bytes']+=min(len(raw),len(plain))+1
 result['dictionary_deflate_bytes']+=min(len(raw),len(packed))+1
 v=by_prefix.setdefault(prefix,[0,0,0]);v[0]+=len(raw);v[1]+=min(len(raw),len(plain))+1;v[2]+=min(len(raw),len(packed))+1
result['prefixes']=[{'path':json.loads(path),'raw':by_prefix[i][0],'plain':by_prefix[i][1],'dictionary':by_prefix[i][2]} for i,path in db.execute('SELECT id,path FROM prefixes') if i in by_prefix]
print(json.dumps(result,ensure_ascii=False,indent=2))
