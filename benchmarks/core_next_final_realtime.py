"""当前 Thread 大正文写入、事件循环与独立 Observation 进程的实时测量。"""
import argparse
import asyncio
import base64
import json
import multiprocessing
from pathlib import Path
import platform
import random
import resource
import tempfile
import time
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA,ThreadStore


def rss():
    value=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if platform.system()=='Darwin' else value*1024

def summary(values):
    ordered=sorted(values)
    return {'samples':len(values),'p95_s':ordered[int(.95*(len(ordered)-1))],'max_s':max(values)}

def observe(path,tid,connection,count):
    from society0.kernel.observation import Observation
    samples=[];seen={};refs={};watermarks=[];cursor=None
    with Observation(path) as reader:
        initial=reader.status();connection.send({'ready':True,'initial':initial})
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            start=time.perf_counter();status=reader.status();page=reader.thread_tail(tid,cursor=cursor,max_bytes=2048)
            end=time.perf_counter();samples.append(end-start);cursor=page['cursor']
            mark=(status['complete']['step'],page['complete_through'],page['total'])
            if not watermarks or tuple(watermarks[-1]['value'])!=mark:watermarks.append({'at':end,'value':mark})
            for item in page['items']:
                if 'payload_ref' in item:
                    seq=item['payload_ref']['seq'];seen[str(seq)]=end;refs[str(seq)]=item['payload_ref']
            if len(seen)==count and status['complete']['step']==1:break
            time.sleep(.005)
        else:raise RuntimeError('observer timed out')
        before_materialize=rss();raw_equal=[]
        expected=base64.b64encode(random.Random(37).randbytes(10*1024*1024*3//4)).decode()
        for seq,ref in refs.items():
            raw=bytearray();offset=0
            while True:
                part=reader.read_thread_payload(ref,offset=offset,size=65536,max_bytes=90000)
                raw.extend(base64.b64decode(part['data']))
                if part['next_offset'] is None:break
                offset=part['next_offset']
            value=json.loads(raw);assert value['content']==expected
            raw_equal.append({'seq':int(seq),'content_bytes':len(value['content']),'equal':True})
        connection.send({'seen':seen,'response':summary(samples),'poll_sleep_s':.005,'watermarks':watermarks,
                         'observer_peak_before_full_read_bytes':before_materialize,'observer_peak_after_full_read_bytes':rss(),'originals':raw_equal})

async def measure(root):
    body=base64.b64encode(random.Random(37).randbytes(10*1024*1024*3//4)).decode()
    with StageStore.create(root,THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',1,'decision')
        context=multiprocessing.get_context('spawn');parent,child=context.Pipe()
        process=context.Process(target=observe,args=(str(root),tid,child,4));process.start()
        ready=await asyncio.to_thread(parent.recv)
        running=True;gaps=[]
        async def heartbeat():
            last=time.perf_counter()
            while running:
                await asyncio.sleep(0)
                now=time.perf_counter();gaps.append(now-last);last=now
        task=asyncio.create_task(heartbeat());await asyncio.sleep(0)
        writes=[]
        for index in range(4):
            start=time.perf_counter();seq=threads.append_message(tid,{'role':'user','content':body});end=time.perf_counter()
            writes.append({'seq':index+2,'start':start,'returned':end,'wall_s':end-start})
            await asyncio.sleep(.02)
        start=time.perf_counter();store.complete(1);complete=time.perf_counter()-start
        await asyncio.sleep(0);running=False;await task
        observed=await asyncio.to_thread(parent.recv);await asyncio.to_thread(process.join,10)
        assert process.exitcode==0
        for write in writes:
            seen=observed['seen'][str(write['seq'])]
            write['start_to_visible_s']=seen-write['start']
            write['return_to_visible_s']=seen-write['returned']
        return {'source':'current checkout; record git identity with invocation','python':platform.python_version(),'platform':platform.platform(),
                'body_bytes':len(body),'messages':4,'writer_peak_rss_bytes':rss(),'event_loop':summary(gaps),
                'writes':writes,'complete_s':complete,'initial_status':ready['initial'],'observer':observed,
                'limits':'local filesystem; 5ms observer sleep is sampling cadence, visibility measured by actual first observation timestamps; return-to-visible may be negative because COMMIT precedes append return; full JSON verification materializes original body and has separate RSS high water'}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='society0-final-realtime-') as directory:
        result=asyncio.run(measure(Path(directory)/'run'))
    Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
