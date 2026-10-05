import json,time,importlib.util,resource,zlib
from pathlib import Path
import apsw
spec=importlib.util.spec_from_file_location('shared_codec','/tmp/society0-acceptance-20261004/shared_codec.py');codec=importlib.util.module_from_spec(spec);spec.loader.exec_module(codec)
source=apsw.Connection('/tmp/society0-core-next-20261004/v04-root/current.sqlite',flags=apsw.SQLITE_OPEN_READONLY)
ordinals=[row[0] for row in source.execute('SELECT seq FROM fixture_entries WHERE seq>=1438000 ORDER BY seq LIMIT 10000')]
ordinals=[4]+ordinals
path=Path('/tmp/society0-acceptance-20261004/shared-sqlite-trial.sqlite');c=apsw.Connection(str(path));c.execute('PRAGMA journal_mode=OFF');c.execute('PRAGMA synchronous=OFF');c.execute('CREATE TABLE records(ordinal INTEGER PRIMARY KEY,raw_start INTEGER NOT NULL,raw_bytes INTEGER NOT NULL)');c.execute('CREATE TABLE blocks(id INTEGER PRIMARY KEY,payload BLOB NOT NULL)')
number=0
def emit(size,body):
 global number
 c.execute('INSERT INTO blocks VALUES(?,?)',(number,body));number+=1
sink=codec.ChunkWriter(emit);started=time.perf_counter()
def value_for(ordinal):
 return json.loads(b''.join(zlib.decompress(body) for body, in source.execute('SELECT payload FROM fixture_chunks WHERE seq=? ORDER BY chunk',(ordinal,))))
with c:
 for ordinal in ordinals:
  start=sink.position;codec.write_json(value_for(ordinal),sink.write);c.execute('INSERT INTO records VALUES(?,?,?)',(ordinal,start,sink.position-start))
 sink.finish()
imported=time.perf_counter()-started;verified=0
for ordinal,start,size in c.execute('SELECT ordinal,raw_start,raw_bytes FROM records ORDER BY raw_start'):
 raw=b''.join(codec.decode_chunk(body) for body, in c.execute('SELECT payload FROM blocks WHERE id BETWEEN ? AND ? ORDER BY id',(start//65536,(start+size-1)//65536)))
 assert json.loads(raw[start%65536:start%65536+size])==value_for(ordinal);verified+=1
c.close();source.close()
print(json.dumps({'source_ordinals':[ordinals[0],ordinals[1],ordinals[-1]],'records':verified,'all_values_equal':True,'logical_bytes':path.stat().st_size,'import_seconds':imported,'blocks':number}))
