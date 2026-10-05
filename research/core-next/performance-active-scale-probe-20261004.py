"""冻结提交上的小活动量对照；每个配置独立进程，不使用模型。"""
import asyncio, importlib.util, json, os, pathlib, statistics, subprocess, sys, tarfile, tempfile
ROOT=pathlib.Path(__file__).resolve().parents[2]
COMMIT='9180498586ae87ce592fb7f6ce78b68cb38a5b59'
if len(sys.argv)>1:
    spec=importlib.util.spec_from_file_location('step_probe',sys.argv[2]);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    with tempfile.TemporaryDirectory(prefix='society0-active-') as temp:
        result=asyncio.run(mod.probe(pathlib.Path(temp)/'run',mode='rule',steps=20,actors=int(sys.argv[1])))
    print(json.dumps(result));sys.exit()
with tempfile.TemporaryDirectory(prefix='society0-frozen-scale-') as temp:
    temp=pathlib.Path(temp); archive=temp/'source.tar'
    archive.write_bytes(subprocess.check_output(['git','archive',COMMIT,'src'],cwd=ROOT))
    with tarfile.open(archive) as tf:tf.extractall(temp,filter='data')
    probe=temp/'step.py';probe.write_bytes((ROOT/'research/core-next/x08-executed-core_next_step_cost.py').read_bytes())
    env=dict(os.environ,PYTHONPATH=str(temp/'src'));cases=[]
    for actors in (1,8,32):
        result=json.loads(subprocess.check_output([sys.executable,__file__,str(actors),str(probe)],env=env,cwd=temp,text=True))
        assert result['restored_equal'] and all(value==20 for _,value in result['balances'])
        cases.append(result)
    target=ROOT/'research/core-next/performance-active-scale-20261004.json'
    target.write_text(json.dumps({'source_commit':COMMIT,'fixed_initial_history':0,'steps':20,'cases':cases,'limits':'independent processes; fixed initial history, growing account count; serial rule phase; no model, memory, shell or network; OS cache retained'},indent=2)+'\n')
