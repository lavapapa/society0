from pathlib import Path
import subprocess,json,time
root=Path('/tmp/society0-acceptance-clean-sqlite-20261004');root.mkdir(exist_ok=True)
results=[]
for name,extra in [('base',''),('llm','llm'),('anthropic','anthropic'),('google','google'),('observe','observe'),('social','social'),('memory','memory'),('shell','shell')]:
 dest=root/name;commands=[['uv','venv','--python',str(Path('.venv/bin/python').resolve()),str(dest)],['uv','pip','install','--python',str(dest/'bin/python'),'.'+('['+extra+']' if extra else '')]]
 start=time.perf_counter();codes=[]
 with (root/(name+'.txt')).open('w') as log:
  for command in commands:
   try:r=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=180);code=r.returncode
   except subprocess.TimeoutExpired:code='timeout'
   codes.append(code)
   if code!=0:break
 result={'name':name,'exit_codes':codes,'seconds':time.perf_counter()-start,'commands':commands};results.append(result)
 (Path('research/core-next/acceptance-20261004')/'clean-install-frozen-results.json').write_text(json.dumps(results,indent=2))
 print(json.dumps(result),flush=True)
