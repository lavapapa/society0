import sqlite3,json,time,heapq
from pathlib import Path
base=Path('/tmp/society0-real-query-review-20260929');db=sqlite3.connect(base/'observation.sqlite');db.execute('PRAGMA cache_size=-4096')
out={'entry_count':db.execute('SELECT count(*) FROM entries').fetchone()[0],'memberships':db.execute('SELECT count(*) FROM scopes').fetchone()[0],'direct_scopes':db.execute('SELECT count(DISTINCT parent) FROM paths JOIN entries ON entries.path_id=paths.id').fetchone()[0]}
dst=sqlite3.connect(base/'direct-members.sqlite');dst.execute('PRAGMA journal_mode=OFF');dst.execute('PRAGMA synchronous=OFF');dst.execute('CREATE TABLE direct(scope INTEGER,ordinal INTEGER,start INTEGER,end INTEGER,entry_id INTEGER,PRIMARY KEY(scope,ordinal,start)) WITHOUT ROWID');dst.execute('CREATE INDEX direct_current ON direct(scope,ordinal) WHERE end IS NULL');dst.execute('ATTACH DATABASE ? AS original',(str(base/'observation.sqlite'),));t=time.perf_counter()
with dst:dst.execute('INSERT INTO direct SELECT p.parent,e.ordinal,e.start,e.end,e.rowid FROM original.entries e JOIN original.paths p ON p.id=e.path_id')
out['build_seconds']=time.perf_counter()-t;out['dbstat']=dict(dst.execute('SELECT name,sum(pgsize) FROM dbstat GROUP BY name'))
node=0
for part in ['environment','state','projections']:node=db.execute('SELECT id FROM paths WHERE parent=? AND part=?',(node,json.dumps(part))).fetchone()[0]
# 从仅有集合节点的闭包取本scope直接集合；不枚举叶条目。
parents=dict(db.execute('SELECT DISTINCT p.id,p.parent FROM paths p JOIN paths child ON child.parent=p.id JOIN entries e ON e.path_id=child.id'))
def inside(n):
 while n!=node and n in parents:n=parents[n]
 return n==node
scopes=[n for n in parents if inside(n)];out['selected_scopes']=len(scopes)
vm=0
def progress():
 global vm
 vm+=1;return 0
dst.set_progress_handler(progress,1);t=time.perf_counter();streams=[iter(dst.execute('SELECT ordinal,entry_id FROM direct INDEXED BY direct_current WHERE scope=? AND end IS NULL AND ordinal>? ORDER BY ordinal',(s,-1))) for s in scopes];page=[]
for item in heapq.merge(*streams):
 page.append(item)
 if len(page)==101:break
out['page_seconds']=time.perf_counter()-t;out['page_vm']=vm
old=list(db.execute('SELECT ordinal,entry_id FROM scopes INDEXED BY scopes_current WHERE scope=? AND end IS NULL ORDER BY ordinal LIMIT 101',(node,)));out['same_order_and_entries']=old==page
Path('/mnt/data/l20/qin/runtime-observation-real-tests-20260929/query-direct-scope-prototype.json').write_text(json.dumps(out));print(json.dumps(out))
