"""真实根的存储机制试验；SQL fixture 不代表产业环境迁移。"""
from __future__ import annotations
import argparse
import gc
from itertools import chain
import json
from pathlib import Path
import resource
import sys
import time
import zlib
from society0.kernel.storage import StageStore
from society0.kernel._json_chunks import CHUNK_BYTES, decode_chunks

SCHEMA=(
    'CREATE TABLE fixture_entries(seq INTEGER PRIMARY KEY,raw_bytes INTEGER NOT NULL,editable INTEGER NOT NULL)',
    'CREATE TABLE fixture_chunks(seq INTEGER NOT NULL,chunk INTEGER NOT NULL,raw_bytes INTEGER NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(seq,chunk))',
    'CREATE TABLE fixture_hot(seq INTEGER PRIMARY KEY,value INTEGER NOT NULL)',
    'CREATE TABLE fixture_selection(kind TEXT NOT NULL,ordinal INTEGER NOT NULL,seq INTEGER NOT NULL,raw_bytes INTEGER NOT NULL,PRIMARY KEY(kind,ordinal))',
)


def _save(writer, sequence, entry, *, replace=False):
    if replace:
        writer.execute('DELETE FROM fixture_chunks WHERE seq=?',(sequence,))
    total=index=0
    def emit(size,payload):
        nonlocal total,index
        writer.execute('INSERT INTO fixture_chunks VALUES(?,?,?,?)',(sequence,index,size,payload))
        total+=size;index+=1
    writer.write_json_chunks(entry,emit)
    editable=int(type(entry.get('value')) is dict)
    if replace:
        writer.execute('UPDATE fixture_entries SET raw_bytes=?,editable=? WHERE seq=?',(total,editable,sequence))
    else:
        writer.execute('INSERT INTO fixture_entries VALUES(?,?,?)',(sequence,total,editable))
        writer.execute('INSERT INTO fixture_hot VALUES(?,0)',(sequence,))
    return total


def build_fixture(path, entries):
    def initialize(writer):
        small=[];large=None
        for sequence,entry in enumerate(entries):
            total=_save(writer,sequence,entry)
            if type(entry.get('value')) is dict:
                if 100<=total<=4096 and len(small)<10:small.append((sequence,total))
                if large is None or total>large[1]:large=(sequence,total)
        writer.executemany('INSERT INTO fixture_selection VALUES(?,?,?,?)',
            [('small',i,seq,total) for i,(seq,total) in enumerate(small)]+([('large',0,*large)] if large else []))
    return StageStore.create(path,SCHEMA,initialize=initialize)


def load_entry(store, sequence):
    return store.read(lambda view:decode_chunks(payload for (payload,) in view.iter_query(
        'SELECT payload FROM fixture_chunks WHERE seq=? ORDER BY chunk',(sequence,))))


def read_range(store, sequence, offset, size):
    def read(view):
        first=offset//CHUNK_BYTES;last=(offset+size-1)//CHUNK_BYTES
        rows=view.query('SELECT chunk,payload FROM fixture_chunks WHERE seq=? AND chunk BETWEEN ? AND ? ORDER BY chunk',
                        (sequence,first,last),max_rows=last-first+1)
        output=bytearray()
        for chunk,payload in rows:
            raw=zlib.decompress(payload)
            start=max(0,offset-chunk*CHUNK_BYTES)
            end=min(len(raw),offset+size-chunk*CHUNK_BYTES)
            output.extend(raw[start:end])
        return bytes(output)
    return store.read(read)


def disk_usage(path):
    groups={}
    for file in Path(path).rglob('*'):
        if not file.is_file():continue
        relative=file.relative_to(path)
        group=relative.parts[0] if len(relative.parts)>1 else str(relative)
        stats=file.stat();entry=groups.setdefault(group,{'logical_bytes':0,'allocated_bytes':0})
        entry['logical_bytes']+=stats.st_size
        entry['allocated_bytes']+=stats.st_blocks*512
    return {'groups':groups,'logical_bytes':sum(x['logical_bytes'] for x in groups.values()),
            'allocated_bytes':sum(x['allocated_bytes'] for x in groups.values())}


def modify_probe(store, sequences, *, mode, steps, mutate=None):
    output=[]
    for _ in range(steps):
        step=store.complete_step+1;cpu=time.process_time();started=time.perf_counter()
        if mode=='whole':
            entries=[(sequence,load_entry(store,sequence)) for sequence in sequences]
            def change(writer):
                for sequence,entry in entries:
                    if mutate is None:entry['value']['_probe_hot']=step
                    else:mutate(entry['value'])
                    _save(writer,sequence,entry,replace=True)
            store.transaction(change)
        elif mode=='split':
            store.transaction(lambda w:w.executemany('UPDATE fixture_hot SET value=? WHERE seq=?',
                                                     ((step,sequence) for sequence in sequences)))
        else:raise ValueError(mode)
        changed=time.perf_counter();memory=store._session.memory_used
        live_disk=disk_usage(store.path)
        previous_fault=store._fault;publish_disk=[];observation_time=[0.0,0.0]
        def capture(phase):
            if phase=='before_publish':
                probe_wall=time.perf_counter();probe_cpu=time.process_time()
                publish_disk.append(disk_usage(store.path))
                observation_time[0]+=time.perf_counter()-probe_wall
                observation_time[1]+=time.process_time()-probe_cpu
            previous_fault(phase)
        store._fault=capture
        complete_start=time.perf_counter();complete_cpu=time.process_time()
        try:descriptor=store.complete(step)
        finally:store._fault=previous_fault
        complete_seconds=time.perf_counter()-complete_start-observation_time[0]
        complete_cpu_seconds=time.process_time()-complete_cpu-observation_time[1]
        output.append({'step':step,'mode':mode,'edit_seconds':changed-started,
            'complete_seconds':complete_seconds,'cpu_seconds':time.process_time()-cpu,
            'complete_cpu_seconds':complete_cpu_seconds,'disk_observation_seconds':observation_time[0],
            'session_bytes_before_complete':memory,'changeset_bytes':(store.path/descriptor['changeset']).stat().st_size,
            'live_disk':live_disk,'before_marker_disk':publish_disk[0],'complete_disk':disk_usage(store.path)})
    return output


def alignment_probe(path, *, body_bytes=10*1024*1024):
    import base64
    import random
    cold=base64.b64encode(random.Random(407).randbytes((body_bytes+3)//4*3)).decode()[:body_bytes]
    output=[]
    for name,first,new in [('tail_length_change',False,10),('front_equal_length',True,8),('front_length_change',True,10)]:
        value={'hot':9,'cold':cold} if first else {'cold':cold,'hot':9}
        original={'path':['fixture'],'operation':'set','value':value}
        with build_fixture(Path(path)/name,[original]) as store:
            result=modify_probe(store,[0],mode='whole',steps=1,mutate=lambda value,n=new:value.update(hot=n))[0]
            actual=load_entry(store,0)
            assert actual['value']==dict(value,hot=new)
            import apsw
            changes={'rows':0,'old_blob_bytes':0,'new_blob_bytes':0,'operations':{}}
            descriptor=store._last
            with (store.path/descriptor['changeset']).open('rb') as stream:
                for change in apsw.Changeset.iter(stream.read):
                    changes['rows']+=1
                    changes['operations'][change.op]=changes['operations'].get(change.op,0)+1
                    for side in ('old','new'):
                        row=getattr(change,side)
                        if row is not None:changes[side+'_blob_bytes']+=sum(len(item) for item in row if type(item) is bytes)
            result.update(case=name,cold_body_bytes=body_bytes,all_values_equal=True,native_changes=changes)
            output.append(result)
    return output


def source_entries(source):
    # 旧格式解析仅发生在独立转换/全值对照进程。
    from benchmarks.real_state_conversion_probe import source_state
    from society0.incremental_checkpoint import PersistenceSchema,_WILDCARD
    from society0.persistence import PersistenceManager
    state,manifest=source_state(source)
    declarations=manifest['root_metadata']['persistence_schemas']
    schema=PersistenceSchema.merge(*(PersistenceSchema.compile(item['schema'],root_path=tuple(
        _WILDCARD if part=='*' else part for part in item['root_path'])) for item in declarations))
    PersistenceManager._v4_apply_transient_defaults(state,schema)
    manager=object.__new__(PersistenceManager);manager._v4_schema=schema
    yield from manager._v4_root_entries(state)


def peak_rss():
    value=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform=='darwin' else value*1024


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=['convert','verify','read','whole','split','restore'],required=True)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--target',type=Path)
    parser.add_argument('--steps',type=int,default=20)
    args=parser.parse_args()
    started=time.perf_counter();cpu=time.process_time()
    result={'mode':args.mode,'scope':'storage fixture; no industry rule migration','python':sys.version}
    if args.mode=='convert':
        entries=source_entries(args.source);first=next(entries)
        result['conversion_prepare_seconds']=time.perf_counter()-started
        result['conversion_prepare_peak_rss_bytes']=peak_rss()
        write_start=time.perf_counter()
        with build_fixture(args.run,chain([first],entries)) as store:
            result['root_write_seconds']=time.perf_counter()-write_start
            result['entries'],result['raw_bytes']=store.read(lambda v:v.query('SELECT count(*),sum(raw_bytes) FROM fixture_entries')[0])
            result['open_disk']=disk_usage(args.run)
    elif args.mode=='restore':
        with StageStore.restore(args.run,args.target) as store:
            result['complete_step']=store.complete_step
            result['open_disk']=disk_usage(args.target)
    else:
        with StageStore.open(args.run) as store:
            if args.mode=='verify':
                count=0
                for sequence,expected in enumerate(source_entries(args.source)):
                    assert load_entry(store,sequence)==expected,sequence
                    count+=1
                assert store.read(lambda v:v.query('SELECT count(*) FROM fixture_entries')[0][0])==count
                result.update(all_entries_equal=True,entries=count)
            else:
                # 固定活动量；选择成本单列，运行不扫描全部历史重建状态。
                select_start=time.perf_counter()
                small=store.read(lambda v:v.query("SELECT seq FROM fixture_selection WHERE kind='small' ORDER BY ordinal"))
                large=store.read(lambda v:v.query("SELECT seq,raw_bytes FROM fixture_selection WHERE kind='large'"))
                result['selection_seconds']=time.perf_counter()-select_start
                result['large_entry']=large
                if args.mode=='read':
                    start=time.perf_counter()
                    for _ in range(100):
                        for (sequence,) in small:store.read(lambda v,s=sequence:v.query('SELECT value FROM fixture_hot WHERE seq=?',(s,)))
                    result['hot_query_seconds']=time.perf_counter()-start
                    result['hot_query_count']=100*len(small)
                    sequence,total=large[0];start=time.perf_counter()
                    data=read_range(store,sequence,max(0,total//2),64)
                    result['cold_range_seconds']=time.perf_counter()-start
                    result['cold_range_bytes']=len(data)
                else:
                    result['steps']=modify_probe(store,[large[0][0]],mode=args.mode,steps=args.steps)
                result['open_disk']=disk_usage(args.run)
    result.update(wall_seconds=time.perf_counter()-started,cpu_seconds=time.process_time()-cpu,peak_rss_bytes=peak_rss(),
                  closed_disk=disk_usage(args.target if args.mode=='restore' else args.run))
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
