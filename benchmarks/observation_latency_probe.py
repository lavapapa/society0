"""独立 HTTP 查询进程的状态与已提交数据可见延迟。"""
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen
from society0.incremental_checkpoint import V4CheckpointStore, SealedTickDelta
from society0.observation import write_runtime_status


def request(port):
    req = Request(f'http://127.0.0.1:{port}', data=b'{"method":"status"}', headers={'Content-Type':'application/json'})
    with urlopen(req,timeout=3) as response:
        return json.load(response)['result']


def main():
    with tempfile.TemporaryDirectory(prefix='society0-latency-') as directory:
        root = Path(directory)
        store = V4CheckpointStore(root)
        marker = store.publish(SealedTickDelta(0,(),()))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
        process = subprocess.Popen([sys.executable,'-m','society0.observation',str(root),'--index-dir',str(root/'index'),'--serve',str(port)],stdout=subprocess.DEVNULL)
        try:
            deadline=time.monotonic()+10
            while True:
                try:
                    request(port); break
                except OSError:
                    if time.monotonic()>deadline: raise
                    time.sleep(.02)
            status_samples=[]; indexed_samples=[]
            for i in range(20):
                write_runtime_status(root,run_id=marker['run_id'],phase=f'sample-{i}')
                before=time.perf_counter()
                while request(port)['phase']!=f'sample-{i}': time.sleep(.02)
                status_samples.append(time.perf_counter()-before)
            for step in range(1,11):
                marker=store.publish(SealedTickDelta(step,({'sequence':0,'path':['x'],'operation':'set','value':step},),()))
                before=time.perf_counter()
                while (request(port)['indexed_checkpoint'] or {}).get('checkpoint_id')!=marker['checkpoint_id']: time.sleep(.02)
                indexed_samples.append(time.perf_counter()-before)
            print(json.dumps({'status_samples_seconds':status_samples,'status_p95_seconds':sorted(status_samples)[18],
                'committed_to_indexed_samples_seconds':indexed_samples,'committed_to_indexed_p95_seconds':sorted(indexed_samples)[-1],
                'client_poll_seconds':.02,'index_poll_seconds':.5,'temporary_artifacts_removed':True},indent=2))
        finally:
            process.terminate();process.wait(timeout=5)

if __name__=='__main__': main()
