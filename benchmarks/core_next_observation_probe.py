"""独立观察短状态的真实 HTTP 与长期 Python reader 成本。"""
import argparse
import http.client
import json
from pathlib import Path
import statistics
import tempfile
import threading
import time

import apsw
from society0.kernel.storage import StageStore
from society0.kernel.observation import Observation,ObservationService,make_server


def measure(table_count,requests):
    with tempfile.TemporaryDirectory(prefix='society0-observer-probe-') as directory:
        path=Path(directory)/'run'
        with StageStore.create(path,[f'CREATE TABLE t{i}(id INTEGER PRIMARY KEY,value TEXT)' for i in range(table_count)]):pass
        original=apsw.Connection;connections=[]
        def connect(*args,**kwargs):
            result=original(*args,**kwargs);connections.append(1);return result
        apsw.Connection=connect
        try:
            results={}
            with ObservationService(path) as service:
                server=make_server(service,port=0)
                worker=threading.Thread(target=server.serve_forever);worker.start()
                try:
                    samples=[];before=len(connections)
                    for _ in range(requests):
                        start=time.perf_counter()
                        client=http.client.HTTPConnection('127.0.0.1',server.server_address[1])
                        client.request('POST','/',json.dumps({'method':'status'}))
                        response=client.getresponse();assert response.status==200
                        assert json.loads(response.read())['complete']['step']==0
                        client.close();samples.append(time.perf_counter()-start)
                    results['http']={'total_s':sum(samples),'p95_ms':sorted(samples)[int(.95*(len(samples)-1))]*1000,'connections':len(connections)-before}
                finally:server.shutdown();server.server_close();worker.join()
            with Observation(path) as observer:
                samples=[];before=len(connections)
                for _ in range(requests):
                    start=time.perf_counter();observer.status();samples.append(time.perf_counter()-start)
                results['python_with']={'total_s':sum(samples),'p95_ms':sorted(samples)[int(.95*(len(samples)-1))]*1000,'connections':len(connections)-before}
            return {'tables':table_count,'requests':requests,**results}
        finally:apsw.Connection=original


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--requests',type=int,default=100)
    args=parser.parse_args()
    data=[measure(count,args.requests) for count in (20,1000)]
    Path(args.output).write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps(data))


if __name__=='__main__':main()
