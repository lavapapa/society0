"""实际组件的只读元数据布局对照：键正文单份、有界块、精确fence定位。"""
import argparse,bisect,gzip,json,random,sqlite3,struct,tempfile,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('component');p.add_argument('--directory');p.add_argument('--compressed',action='store_true');a=p.parse_args()
source=Path(a.component).resolve()
with tempfile.TemporaryDirectory(dir=a.directory) as directory:
 path=Path(directory)/'dictionary.sqlite';db=sqlite3.connect(path)
 db.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=FILE; PRAGMA cache_size=-2048; CREATE TABLE prefixes(id INTEGER PRIMARY KEY,path TEXT UNIQUE); CREATE TABLE key_blocks(id INTEGER PRIMARY KEY,prefix_id INTEGER,first_leaf TEXT,payload BLOB); CREATE INDEX key_fence ON key_blocks(prefix_id,first_leaf); CREATE TABLE records(sequence INTEGER PRIMARY KEY,path_id INTEGER,operation,raw_bytes INTEGER,record_id TEXT,value_flags INTEGER,raw_start INTEGER); CREATE TEMP TABLE keymap(prefix_id INTEGER,leaf TEXT,path_id INTEGER,PRIMARY KEY(prefix_id,leaf)) WITHOUT ROWID;')
 db.execute('ATTACH DATABASE ? AS source',(source.as_uri()+'?mode=ro&immutable=1',));db.execute('INSERT INTO prefixes SELECT * FROM source.prefixes');db.commit()
 started=time.perf_counter();buffer=bytearray();prefix=None;first=None;start_id=0;path_id=0;batch=[];blocks=0
 def flush_mapping():
  db.executemany('INSERT INTO keymap VALUES (?,?,?)',batch);batch.clear()
 def emit():
  nonlocal_dummy=None
  if not buffer:return
  db.execute('INSERT INTO key_blocks VALUES (?,?,?,?)',(start_id,prefix,first,gzip.compress(buffer,compresslevel=3,mtime=0) if a.compressed else buffer))
  buffer.clear()
 with db:
  for prefix_id,leaf,count in db.execute('SELECT prefix_id,leaf,count(*) FROM source.records GROUP BY prefix_id,leaf ORDER BY prefix_id,leaf'):
   raw=leaf.encode();entry=struct.pack('<Q',count)+raw+b'\0'
   if prefix_id!=prefix or len(buffer)+len(entry)>65536:
    emit();prefix=prefix_id;first=leaf;start_id=path_id;blocks+=1
   buffer.extend(entry);batch.append((prefix_id,leaf,path_id));path_id+=1
   if len(batch)==256:flush_mapping()
  emit()
  if batch:flush_mapping()
  db.execute('INSERT INTO records SELECT r.sequence,k.path_id,r.operation,r.raw_bytes,r.record_id,r.value_flags,r.raw_start FROM source.records r CROSS JOIN keymap k ON k.prefix_id=r.prefix_id AND k.leaf=r.leaf')
  db.execute('CREATE INDEX records_path ON records(path_id,sequence)')
 elapsed=time.perf_counter()-started
 samples=[];decode_seconds=0;query_seconds=0
 def unpack(data):
  if a.compressed:data=gzip.decompress(data)
  keys=[];counts=[];position=0
  while position<len(data):
   counts.append(struct.unpack_from('<Q',data,position)[0]);position+=8
   end=data.index(0,position);keys.append(data[position:end].decode());position=end+1
  return keys,counts
 count=db.execute('SELECT count(*) FROM records').fetchone()[0]
 for i in range(100):
  sequence=(i*104729)%count
  prefix_id,leaf=db.execute('SELECT prefix_id,leaf FROM source.records WHERE sequence=?',(sequence,)).fetchone()
  tick=time.perf_counter();row=db.execute('SELECT id,payload FROM key_blocks WHERE prefix_id=? AND first_leaf<=? ORDER BY first_leaf DESC LIMIT 1',(prefix_id,leaf)).fetchone();keys,counts=unpack(row[1]);slot=bisect.bisect_left(keys,leaf);assert keys[slot]==leaf
  actual=db.execute('SELECT count(*) FROM records WHERE path_id=?',(row[0]+slot,)).fetchone()[0];assert actual==counts[slot]
  samples.append(time.perf_counter()-tick)
 boundaries=list(db.execute('SELECT id,length(payload) FROM key_blocks ORDER BY id'));starts=[r[0] for r in boundaries];last=-1;misses=0;compressed_reads=0
 for (key_id,) in db.execute('SELECT path_id FROM records ORDER BY sequence'):
  block=bisect.bisect_right(starts,key_id)-1
  if block!=last:misses+=1;compressed_reads+=boundaries[block][1];last=block
 print(json.dumps({'sequence_block_misses':misses,'sequence_compressed_bytes_read':compressed_reads,'dictionary_compressed_bytes':sum(r[1] for r in boundaries),'compressed':a.compressed,'keys':path_id,'blocks':blocks,'metadata_file_bytes':path.stat().st_size,'build_seconds':elapsed,'table_bytes':dict(db.execute('SELECT name,sum(pgsize) FROM dbstat GROUP BY name')),'exact_query_median_seconds':sorted(samples)[50],'exact_query_p95_seconds':sorted(samples)[94]},indent=2))
 db.close()
