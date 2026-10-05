import subprocess,os,json,time
from pathlib import Path
root=Path('/mnt/data/l20/qin/society0-acceptance-20261004');env=dict(os.environ);env.pop('PYTHONPATH',None);env['PIP_CACHE_DIR']='/tmp/society0-acceptance-20261004/pip-cache';dest=Path('/tmp/society0-acceptance-20261004/base-final');results=[]
commands=[['python','-m','venv',str(dest)],[str(dest/'bin/python'),'-m','pip','install',str(root/'source')],[str(dest/'bin/python'),'/tmp/society0-acceptance-20261004/clean_consumer.py','base'],['/tmp/society0-acceptance-20261004/all/bin/python','-m','pip','install','--force-reinstall','--no-deps',str(root/'source')]]
for command in commands:
 start=time.perf_counter()
 try:r=subprocess.run(command,env=env,cwd='/tmp',capture_output=True,text=True,timeout=180);result={'command':command,'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr,'seconds':time.perf_counter()-start}
 except subprocess.TimeoutExpired:result={'command':command,'exit_code':'timeout','seconds':time.perf_counter()-start}
 results.append(result);(root/'linux-final-wheel-consumers.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(json.dumps({'command':command,'exit_code':result['exit_code']}),flush=True)
 if result['exit_code']!=0:break
