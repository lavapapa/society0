"""已压缩多步epoch合并：同负载比较索引重复构建及随机范围读取。"""
import argparse
import importlib.util
import inspect
import json
import resource
import sys
import tempfile
import time
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--module',required=True)
p.add_argument('--directory')
p.add_argument('--count',type=int,default=20000)
p.add_argument('--steps',type=int,default=5)
a=p.parse_args()
spec=importlib.util.spec_from_file_location('records',a.module)
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
with tempfile.TemporaryDirectory(dir=a.directory) as d:
 root=Path(d);paths=[]
 before=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
 start=time.perf_counter()
 for step in range(a.steps):
  path=root/f'{step}.sqlite';paths.append(path)
  r.write_records(path,({'sequence':step*a.count+i,'path':['state',str(i)],'operation':'set','value':{'step':step,'i':i,'text':'业务事实'*60}} for i in range(a.count)),**({'pending':True,'record_count':a.count} if 'record_count' in inspect.signature(r.write_records).parameters else {}))
 stage=time.perf_counter()-start
 start=time.perf_counter();r.merge_records(root/'epoch.sqlite',paths);merge=time.perf_counter()-start
 page=r.read_page(root/'epoch.sqlite',path_filter=['state','199'],max_bytes=100000)
 assert page['total']==a.steps and [x['value']['step'] for x in page['records']]==list(range(a.steps))
 peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
 print(json.dumps({'count_per_step':a.count,'steps':a.steps,'stage_seconds':stage,'merge_seconds':merge,'total_seconds':stage+merge,'staged_bytes':sum(p.stat().st_size for p in paths),'final_bytes':(root/'epoch.sqlite').stat().st_size,'rss_extra_bytes':(peak-before)*(1 if sys.platform=='darwin' else 1024)},indent=2))
