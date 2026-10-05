import sqlite3,zlib,json,time
from pathlib import Path
base=Path('/tmp/society0-real-query-review-20260929')
src=sqlite3.connect(f'file:{base}/observation.sqlite?mode=ro',uri=True)
report={}
def pack(s):
 b=s.encode()
 if len(b)>96:
  z=zlib.compress(b,1)
  if len(z)<len(b):return b'z'+z
 return b'j'+b
for mode in ['compressed','dictionary']:
 db=sqlite3.connect(base/f'paths-{mode}.sqlite');db.execute('PRAGMA journal_mode=OFF');db.execute('PRAGMA synchronous=OFF');db.execute('PRAGMA cache_size=-4096')
 if mode=='compressed':db.execute('CREATE TABLE paths(id INTEGER PRIMARY KEY,parent INTEGER,part BLOB,UNIQUE(parent,part))')
 else:
  db.execute('CREATE TABLE parts(id INTEGER PRIMARY KEY,value BLOB UNIQUE)');db.execute('CREATE TABLE paths(id INTEGER PRIMARY KEY,parent INTEGER,part INTEGER,UNIQUE(parent,part))')
 t=time.perf_counter();encode=0
 with db:
  for id,parent,part in src.execute('SELECT id,parent,part FROM paths'):
   t0=time.perf_counter();value=pack(part);encode+=time.perf_counter()-t0
   if mode=='dictionary':
    db.execute('INSERT OR IGNORE INTO parts(value) VALUES (?)',(value,));value=db.execute('SELECT id FROM parts WHERE value=?',(value,)).fetchone()[0]
   db.execute('INSERT INTO paths VALUES (?,?,?)',(id,parent,value))
 report[mode]={'seconds':time.perf_counter()-t,'encode_seconds':encode,'dbstat':dict(db.execute('SELECT name,sum(pgsize) FROM dbstat GROUP BY name'))}
 if mode=='dictionary':report[mode]['unique_parts']=db.execute('SELECT count(*) FROM parts').fetchone()[0]
 db.close()
Path('/mnt/data/l20/qin/runtime-observation-real-tests-20260929/query-path-candidates.json').write_text(json.dumps(report));print(json.dumps(report))
