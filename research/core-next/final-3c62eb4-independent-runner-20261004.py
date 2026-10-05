"""最终冻结源码的独立离线复验；每项退出码单独保存。"""
import datetime,json,os,pathlib,subprocess,tarfile,time
ROOT=pathlib.Path(__file__).resolve().parents[2];OUT=ROOT/'research/core-next'
COMMIT='3c62eb473b59dd3c3ffabb4ab5b1816a3afbdc9d'
DEST=pathlib.Path('/tmp/society0-final-3c62eb4-storage-20261004');DEST.mkdir(exist_ok=False)
archive=DEST/'source.tar';archive.write_bytes(subprocess.check_output(['git','archive',COMMIT],cwd=ROOT))
with tarfile.open(archive) as f:f.extractall(DEST,filter='data')
archive.unlink()
(DEST/'tools/workbench-template/node_modules').symlink_to(ROOT/'tools/workbench-template/node_modules',target_is_directory=True)
python=str(ROOT/'.venv/bin/python');env=dict(os.environ,PYTHONPATH=str(DEST/'src'),SOCIETY0_RUN_REAL_E2E='0')
summary={'commit':COMMIT,'source_dir':str(DEST),'created_at':datetime.datetime.now().astimezone().isoformat(),'python':python,'real_network':False,'results':[]}
summary['head_at_start']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
summary['origin_at_start']=subprocess.check_output(['git','rev-parse','origin/codex/society0-core-next'],cwd=ROOT,text=True).strip()
tracked=subprocess.check_output(['git','ls-tree','-r','--name-only',COMMIT,'src','native'],cwd=ROOT,text=True).splitlines()
summary['source_byte_comparison']={'files':len(tracked),'all_equal':all((DEST/f).read_bytes()==subprocess.check_output(['git','show',COMMIT+':'+f],cwd=ROOT) for f in tracked)}
def run(name,cmd,runenv=env):
 path=OUT/f'final-3c62eb4-{name}-20261004.txt';start=time.perf_counter()
 with path.open('w') as log:
  log.write('CWD: '+str(DEST)+'\nCOMMAND: '+repr(cmd)+'\n');log.flush()
  result=subprocess.run(cmd,cwd=DEST,env=runenv,stdout=log,stderr=subprocess.STDOUT)
  log.write('\nEXIT: '+str(result.returncode)+'\n')
 summary['results'].append({'name':name,'command':cmd,'exit_code':result.returncode,'wall_seconds':time.perf_counter()-start,'log':str(path.relative_to(ROOT))})
 (OUT/'final-3c62eb4-independent-results-20261004.json').write_text(json.dumps(summary,indent=2)+'\n')
run('deterministic',[python,'-m','pytest','-o','addopts=','-q','-rs','tests/primary','tests/e2e','-m','not real_e2e'])
run('experiments',[python,'-m','pytest','-o','addopts=','-q','-rs','tests/experiments'])
venv='/tmp/society0-vector-study-20261004/lib/python3.12/site-packages'
run('vector',[python,'-m','pytest','-o','addopts=','-q','-rs','tests/experiments/test_core_next_vector_backends.py'],dict(env,PYTHONPATH=str(DEST/'src')+os.pathsep+venv))
config=DEST/'pilot-config.json';config.write_text(json.dumps({'release':{'commit':COMMIT},'start':1,'end':2}))
run('pilot',[python,'-m','society0.kernel.runner','--factory','examples.core_next.conversation_pilot:build','--config',str(config),'--output',str(DEST/'pilot-run')])
run('payload',[python,'-m','society0.kernel.workbench','--run',str(DEST/'pilot-run'),'--version','final-independent','--steps','1','2','--actors','a','b','c','d','--output',str(DEST/'pilot-payload.json')])
run('node',['npm','test','--prefix','tools/workbench-template'],dict(env,CORE_NEXT_WORKBENCH_PAYLOAD=str(DEST/'pilot-payload.json')))
summary['skip_contract']={'deterministic':'real_e2e explicitly deselected; no model network','experiments':'sqlite_vec module skipped in normal environment then separately executed','node':'actual generated pilot payload supplied; no intended skip'}
(OUT/'final-3c62eb4-independent-results-20261004.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
