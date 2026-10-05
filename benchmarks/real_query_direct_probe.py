import importlib.util,sys,json,time,resource,threading
from pathlib import Path
spec=importlib.util.spec_from_file_location('society0._direct_observation','/mnt/data/l20/qin/runtime-observation-real-tests-20260929/observation-direct.py');module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
reader_spec=importlib.util.spec_from_file_location('society0._session_records','/mnt/data/l20/qin/runtime-observation-real-tests-20260929/checkpoint-records-session.py')
reader_module=importlib.util.module_from_spec(reader_spec);reader_spec.loader.exec_module(reader_module)
from society0 import checkpoint_records
checkpoint_records.RecordReader=reader_module.RecordReader
root=Path('/mnt/data/l20/qin/runtime-observation-real-tests-20260929/converted-state');index=Path('/tmp/society0-real-query-direct-final-20260929');peak={};stop=threading.Event()
def sizes():return {p.name:p.stat().st_size for p in index.glob('observation.sqlite*')}
def monitor():
 while not stop.wait(.02):
  for name,size in sizes().items():peak[name]=max(size,peak.get(name,0))
thread=threading.Thread(target=monitor);thread.start();t=time.perf_counter()
with module.ObservationReader(root,index_dir=index) as r:
 r.sync();out={'seconds':time.perf_counter()-t,'dbstat':dict(r.db.execute('SELECT name,sum(pgsize) FROM dbstat GROUP BY name'))}
 pages=[]
 for i in range(10):
  t=time.perf_counter();page=r.state_page(path=['environment','state','projections'],limit=100,max_bytes=65536);pages.append(time.perf_counter()-t)
 out.update(page_seconds=pages,total=page['total'],page_bytes=page['bytes'],peak_rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,files_live=sizes())
 out['parts']=r.db.execute('SELECT count(*) FROM parts').fetchone()[0];out['memberships']=r.db.execute('SELECT count(*) FROM scopes').fetchone()[0]
stop.set();thread.join();out.update(files_peak=peak,files_close=sizes())
Path('/mnt/data/l20/qin/runtime-observation-real-tests-20260929/query-real-direct-final.json').write_text(json.dumps(out));print(json.dumps(out))
