"""RapidJSON 1.25 回调失败行为；隔离依赖 target，非产品代码。"""
import json
import sys
import time
import rapidjson

if len(sys.argv)==1:
    import subprocess
    results=[]
    for shape in ('single_string','two_values'):
        for guarded in (False,True):
            child=subprocess.run([sys.executable,__file__,shape,str(int(guarded))],capture_output=True,text=True)
            results.append(dict(shape=shape,guarded=guarded,exit_code=child.returncode,stdout=child.stdout,stderr=child.stderr))
    print(json.dumps({'rapidjson':rapidjson.__version__,'results':results},indent=2))
    raise SystemExit
results=[]
for shape,guarded in [(sys.argv[1],bool(int(sys.argv[2])))]:
    tail='汉🙂'*1000000
    value=tail if shape=='single_string' else ['x'*100000,tail]
    before=sys.getsizeof(tail)
    class Sink:
        calls=0
        effects=0
        error=None
        def write(self,body):
            self.calls+=1
            if self.error is not None:return
            try:
                self.effects+=1
                if self.effects==1:raise OSError('sink failed')
            except BaseException as error:
                if not guarded:raise
                self.error=error
    sink=Sink();start=time.perf_counter();outcome='returned'
    try:
        rapidjson.dump(value,sink,ensure_ascii=False,chunk_size=65536)
        if sink.error is not None:raise sink.error
    except BaseException as error:
        outcome=type(error).__name__
    results.append(dict(shape=shape,guarded=guarded,outcome=outcome,calls=sink.calls,effects=sink.effects,
                        wall_seconds=time.perf_counter()-start,tail_size_before=before,
                        tail_size_after=sys.getsizeof(tail)))
print(json.dumps({'rapidjson':rapidjson.__version__,'python':sys.version,'results':results},indent=2))
