from pathlib import Path
import json, os, re, shutil, subprocess, sys
root=Path(__file__).resolve().parents[3]
out=root/'research/core-next/filesystem-implementation-20261005'
suffix=sys.argv[1] if len(sys.argv)>1 else 'initial'
work=Path('/tmp/society0-f08-base-consumer-'+suffix)
work.mkdir()
shutil.copytree(root/'examples/core_next',work/'examples/core_next',ignore=shutil.ignore_patterns('__pycache__'))
python=Path(sys.argv[2]) if len(sys.argv)>2 else Path('/tmp/society0-f08-base-20261005/bin/python')
env={**os.environ,'PATH':str(python.parent)+os.pathsep+os.environ['PATH'],'SOCIETY0_SOURCE_COMMIT':os.environ.get('SOCIETY0_SOURCE_COMMIT','source-delivery.tar.gz')}
env.pop('PYTHONPATH',None)
commands=re.findall(r'```sh\n(.*?)\n```',(root/'docs/core-next/getting-started.md').read_text().split('## 二、运行',1)[1],re.S)
with (out/('base-tutorial-'+suffix+'.txt')).open('w') as log:
 for command in commands:
  result=subprocess.run(['bash','-eu','-c',command],cwd=work,env=env,text=True,stdout=log,stderr=subprocess.STDOUT)
  assert result.returncode==0,command
 probe="""import importlib.util,json,sys,society0
from pathlib import Path
from society0.kernel.observation import Observation
for name in ('pydantic_ai','openai','chromadb','networkx','society0.core_data','society0.agent','society0.env'):
 assert importlib.util.find_spec(name) is None,name
assert '/site-packages/' in society0.__file__,society0.__file__
with Observation('runs/first-study/resumed') as observation:
 assert observation.status()['complete']['step']==2
print(json.dumps({'wheel_import':society0.__file__,'complete_step':2,'base_only':True},ensure_ascii=False))
"""
 result=subprocess.run([str(python),'-c',probe],cwd=work,env=env,text=True,stdout=log,stderr=subprocess.STDOUT)
 assert result.returncode==0
print('external wheel tutorial completed')
