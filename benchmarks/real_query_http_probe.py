"""真实不可变根的冷索引、HTTP状态延迟和热页；工件明确保留。"""
import argparse,json,os,signal,socket,subprocess,sys,threading,time,urllib.request
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('run');parser.add_argument('index');parser.add_argument('output')
args=parser.parse_args();index=Path(args.index);out=Path(args.output)
with socket.socket() as socket_:
    socket_.bind(('127.0.0.1',0));port=socket_.getsockname()[1]
measurement=out.with_suffix('.sync.json')
server_script='''import json,time,sys
from pathlib import Path
from society0.observation import ObservationReader,main
original=ObservationReader.sync
measurement=Path(sys.argv.pop(1))
def measured(self):
 start=time.perf_counter();result=original(self)
 if not measurement.exists():
  pending=measurement.with_suffix(".tmp");pending.write_text(json.dumps({"sync_seconds":time.perf_counter()-start}));pending.replace(measurement)
 return result
ObservationReader.sync=measured
main()
'''
log=out.with_suffix('.server.log').open('w')
process=subprocess.Popen([sys.executable,'-c',server_script,str(measurement),args.run,'--index-dir',args.index,'--serve',str(port)],stderr=log)
stop=threading.Event();peaks={};max_total=0;rss=0

def sizes():
    return {path.name:path.stat().st_size for path in index.glob('observation.sqlite*')}

def monitor():
    global max_total,rss
    while not stop.wait(.02):
        files=sizes();max_total=max(max_total,sum(files.values()))
        for name,size in files.items():peaks[name]=max(peaks.get(name,0),size)
        try:
            for line in Path(f'/proc/{process.pid}/status').read_text().splitlines():
                if line.startswith('VmHWM:'):rss=max(rss,int(line.split()[1])*1024)
        except FileNotFoundError:pass
thread=threading.Thread(target=monitor);thread.start()

def request(method,params=None):
    data=json.dumps({'method':method,'params':params or {}}).encode()
    req=urllib.request.Request(f'http://127.0.0.1:{port}',data=data)
    with urllib.request.urlopen(req,timeout=3) as response:return json.load(response)
try:
    start=time.perf_counter();latencies=[];seen_unindexed=False;first_indexed=None
    while True:
        before=time.perf_counter()
        try:status=request('status')['result']
        except OSError:
            if process.poll() is not None:raise RuntimeError('server failed')
            if time.perf_counter()-start>10:raise
            time.sleep(.02);continue
        latencies.append(time.perf_counter()-before)
        if status.get('index_error'):raise RuntimeError(status['index_error'])
        if status['indexed_checkpoint']:
            if first_indexed is None:first_indexed=time.perf_counter()-start
            if measurement.exists():break
        else:seen_unindexed=True
        if time.perf_counter()-start>600:raise TimeoutError('index timeout')
        time.sleep(.25)
    pages=[]
    for _ in range(10):
        before=time.perf_counter();page=request('state_page',{'path':['environment','state','projections'],'limit':100,'max_bytes':65536})['result'];pages.append(time.perf_counter()-before)
    result={'run':args.run,'index':args.index,'scope':'full v3 source; immutable state-only converted root',
      'sync_seconds':json.loads(measurement.read_text())['sync_seconds'],'first_indexed_seconds':first_indexed,'seen_unindexed':seen_unindexed,
      'status_latency_seconds':latencies,'status_max_seconds':max(latencies),
      'page_seconds':pages,'total':page['total'],'page_bytes':page['bytes'],
      'peak_rss_bytes':rss,'files_peak':peaks,'peak_total_file_bytes':max_total,'files_ready':sizes()}
finally:
    stop.set();thread.join();process.send_signal(signal.SIGINT);process.wait(10);log.close()
result['files_after_server_exit']=sizes();out.write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='status_latency_seconds'}))
