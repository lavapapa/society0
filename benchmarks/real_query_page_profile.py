import importlib.util,sys,time,json,statistics
from pathlib import Path
base=Path('/mnt/data/l20/qin/runtime-observation-real-tests-20260929')
spec=importlib.util.spec_from_file_location('society0._direct_observation',base/'observation-direct.py');m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
rs=importlib.util.spec_from_file_location('society0._session_records',base/'checkpoint-records-session.py');rm=importlib.util.module_from_spec(rs);rs.loader.exec_module(rm)
from society0 import checkpoint_records
checkpoint_records.RecordReader=rm.RecordReader
stats={}
def timed(owner,name):
 original=getattr(owner,name);stats[name]={'calls':0,'seconds':0}
 def wrapper(*args,**kw):
  before=time.perf_counter()
  try:return original(*args,**kw)
  finally:stats[name]['calls']+=1;stats[name]['seconds']+=time.perf_counter()-before
 setattr(owner,name,wrapper)
timed(m,'_unpack_part');timed(m,'_pack_json');timed(rm.gzip,'decompress');timed(rm,'_open')
with m.ObservationReader(base/'converted-state',index_dir='/tmp/society0-real-query-direct-final-20260929',readonly_index=True) as reader:
 times=[];cpu=time.process_time()
 for i in range(10):
  before=time.perf_counter();page=reader.state_page(path=['environment','state','projections'],limit=100,max_bytes=65536);times.append(time.perf_counter()-before)
 result={'page_seconds':times,'cpu_seconds':time.process_time()-cpu,'profile':stats,'total':page['total'],'bytes':page['bytes']}
(base/'query-real-page-final.json').write_text(json.dumps(result));print(json.dumps(result))
