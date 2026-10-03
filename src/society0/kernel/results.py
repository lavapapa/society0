"""完整步骤结果：逐行写入与有界读取，共用规范 writer 编码。"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass,field
from itertools import chain,islice
import json
import uuid
import zlib

RESULTS_SCHEMA=(
    'CREATE TABLE result_steps(step INTEGER PRIMARY KEY NOT NULL,time TEXT NOT NULL,phase_count INTEGER NOT NULL,activation_count INTEGER NOT NULL,elapsed_s REAL NOT NULL,capacity INTEGER NOT NULL,max_activations INTEGER)',
    'CREATE TABLE result_sets(id TEXT PRIMARY KEY NOT NULL,origin TEXT NOT NULL,step INTEGER NOT NULL,phase_index INTEGER NOT NULL,name TEXT NOT NULL,kind TEXT NOT NULL,count INTEGER NOT NULL,finished INTEGER NOT NULL)',
    'CREATE INDEX result_sets_phase ON result_sets(step,phase_index,kind)',
    'CREATE TABLE result_rows(set_id TEXT NOT NULL,ordinal INTEGER NOT NULL,raw_bytes INTEGER NOT NULL,PRIMARY KEY(set_id,ordinal))',
    'CREATE TABLE result_chunks(set_id TEXT NOT NULL,ordinal INTEGER NOT NULL,chunk INTEGER NOT NULL,raw_start INTEGER NOT NULL,raw_bytes INTEGER NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(set_id,ordinal,chunk))',
    'CREATE TABLE result_phases(step INTEGER NOT NULL,ordinal INTEGER NOT NULL,name TEXT NOT NULL,header TEXT NOT NULL,elapsed_s REAL NOT NULL,PRIMARY KEY(step,ordinal))',
    'CREATE TABLE result_totals(name TEXT PRIMARY KEY NOT NULL,value INTEGER NOT NULL)',
    'CREATE TABLE result_current_metrics(phase TEXT NOT NULL,name TEXT NOT NULL,set_id TEXT NOT NULL,ordinal INTEGER NOT NULL,PRIMARY KEY(phase,name))',
)


@dataclass
class StepResult:
    metrics: dict=field(default_factory=dict)
    tables: dict=field(default_factory=dict)
    artifacts: dict=field(default_factory=dict)
    observations: dict=field(default_factory=dict)
    notes: str|None=None


class Results:
    def __init__(self,store):self.store=store

    @staticmethod
    def _row(writer,identifier,ordinal,value):
        offset=0
        def chunks():
            nonlocal offset
            for number,(size,body) in enumerate(writer.encode_chunks(value)):
                yield identifier,ordinal,number,offset,size,body
                offset+=size
        writer.executemany('INSERT INTO result_chunks VALUES(?,?,?,?,?,?)',chunks())
        writer.execute('INSERT INTO result_rows VALUES(?,?,?)',(identifier,ordinal,offset))

    async def _dataset(self,step,phase_index,name,kind,rows):
        identifier=uuid.uuid4().hex
        origin=self.store.read(lambda r:r.run_id)
        self.store.transaction(lambda w:w.execute('INSERT INTO result_sets VALUES(?,?,?,?,?,?,0,0)',
            (identifier,origin,step,phase_index,name,kind)))
        source=iter(rows);ordinal=0
        while True:
            try:first=next(source)
            except StopIteration:break
            def batch(writer):
                count=0
                outcomes={}
                for value in chain((first,),islice(source,63)):
                    self._row(writer,identifier,ordinal+count,value)
                    count+=1
                    if kind=='activations':
                        outcomes[value['status']]=outcomes.get(value['status'],0)+1
                if kind=='activations':
                    for label,amount in (('activation',count),*outcomes.items()):
                        if amount:
                            writer.execute("INSERT INTO result_totals VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=value+excluded.value",(label+'_count',amount))
                writer.execute('UPDATE result_sets SET count=count+? WHERE id=?',(count,identifier))
                if kind=='table':
                    writer.execute("INSERT INTO result_totals VALUES('row_count',?) ON CONFLICT(name) DO UPDATE SET value=value+excluded.value",(count,))
                return count
            ordinal+=self.store.transaction(batch)
            await asyncio.sleep(0)
        self.store.transaction(lambda w:w.execute('UPDATE result_sets SET finished=1 WHERE id=?',(identifier,)))
        return {'kind':'result_set','id':identifier,'origin':origin}

    async def write_phase(self,step,phase_index,name,result,*,activations=(),elapsed_s=0,capacity=None,concurrency_source=None):
        if result is None:result=StepResult()
        if not isinstance(result,StepResult):raise TypeError('phase must return StepResult or None')
        tables={}
        for label,rows in result.tables.items():
            tables[label]=await self._dataset(step,phase_index,label,'table',rows)
        actors=await self._dataset(step,phase_index,'activations','activations',activations)
        metrics=await self._dataset(step,phase_index,'metrics','metrics',
            ({'name':key,'value':value} for key,value in result.metrics.items()))
        header={'metrics':metrics,'tables':tables,'artifacts':result.artifacts,
                'observations':result.observations,'notes':result.notes,'activations':actors,
                'capacity':capacity,'concurrency_source':concurrency_source}
        reference=await self._dataset(step,phase_index,'header','header',(header,))
        def finish(writer):
            writer.execute('INSERT INTO result_phases VALUES(?,?,?,?,?)',(step,phase_index,name,reference['id'],elapsed_s))
            writer.execute("INSERT INTO result_totals VALUES('phase_count',1) ON CONFLICT(name) DO UPDATE SET value=value+1")
            writer.execute('DELETE FROM result_current_metrics WHERE phase=?',(name,))
            for ordinal,key in enumerate(result.metrics):
                writer.execute('INSERT INTO result_current_metrics VALUES(?,?,?,?) ON CONFLICT(phase,name) DO UPDATE SET set_id=excluded.set_id,ordinal=excluded.ordinal',
                               (name,key,metrics['id'],ordinal))
        self.store.transaction(finish)
        return header

    @staticmethod
    def _identity(view,reference):
        rows=view.query('SELECT origin,count,finished FROM result_sets WHERE id=?',(reference['id'],),max_rows=1)
        if not rows or rows[0][0]!=reference['origin']:raise ValueError('result reference unavailable')
        return rows[0]

    @staticmethod
    def _value(view,identifier,ordinal):
        from ._json_chunks import decode_chunks
        return decode_chunks(body for body, in view.iter_query('SELECT payload FROM result_chunks WHERE set_id=? AND ordinal=? ORDER BY chunk',(identifier,ordinal)))

    def phase(self,step,phase_index):
        def read(view):
            rows=view.query('SELECT header FROM result_phases WHERE step=? AND ordinal=?',(step,phase_index),max_rows=1)
            if not rows:raise KeyError((step,phase_index))
            return self._value(view,rows[0][0],0)
        return self.store.read(read)

    def metric(self,phase,name):
        """精确读取一个当前指标；大值仍由调用者明确请求。"""
        def read(view):
            rows=view.query('SELECT set_id,ordinal FROM result_current_metrics WHERE phase=? AND name=?',(phase,name),max_rows=1)
            if not rows:raise KeyError((phase,name))
            return self._value(view,*rows[0])['value']
        return self.store.read(read)

    def write_step(self,step,time,*,phase_count,activation_count,elapsed_s,capacity,max_activations):
        self.store.transaction(lambda writer:writer.execute('INSERT INTO result_steps VALUES(?,?,?,?,?,?,?)',
            (step,json.dumps(time,ensure_ascii=False,separators=(',',':')),phase_count,activation_count,elapsed_s,capacity,max_activations)))

    def step(self,step):
        def read(view):
            rows=view.query('SELECT time,phase_count,activation_count,elapsed_s,capacity,max_activations FROM result_steps WHERE step=?',(step,),max_rows=1)
            if not rows:raise KeyError(step)
            row=rows[0]
            return dict(step=step,time=json.loads(row[0]),phase_count=row[1],activation_count=row[2],elapsed_s=row[3],capacity=row[4],max_activations=row[5])
        return self.store.read(read)

    def summary(self):
        return self.store.read(lambda r:dict(r.query('SELECT name,value FROM result_totals')))

    def page(self,reference,*,cursor=None,limit=100,max_bytes=65536):
        if type(limit) is not int or limit<1 or type(max_bytes) is not int or max_bytes<512:
            raise ValueError('positive limit and at least 512 page bytes required')
        def read(view):
            origin,count,finished=self._identity(view,reference)
            identity=[view.run_id,reference['id'],origin]
            if cursor is not None and cursor['identity']!=identity:raise ValueError('result cursor mismatch')
            through=count if cursor is None else cursor['through']
            after=-1 if cursor is None else cursor['after']
            if type(through) is not int or not 0<=through<=count or type(after) is not int or not -1<=after<max(through,1):
                raise ValueError('invalid result cursor')
            items=[];last=after;item_bytes=0
            def envelope(end,values):
                continuation={'identity':identity,'through':through,'after':end} if end+1<through else None
                return {'items':values,'total':through,'next_cursor':continuation,'finished':bool(finished)}
            for ordinal,size in view.iter_query('SELECT ordinal,raw_bytes FROM result_rows WHERE set_id=? AND ordinal>? AND ordinal<? ORDER BY ordinal LIMIT ?',
                                                (reference['id'],after,through,limit)):
                ref={'kind':'record_ref','id':reference['id'],'origin':origin,'ordinal':ordinal,'total_bytes':size}
                value=self._value(view,reference['id'],ordinal) if size<=max_bytes else ref
                def encoded_size(item):
                    return len(json.dumps(item,ensure_ascii=False,separators=(',',':')).encode())
                size=encoded_size(value)
                overhead=encoded_size(envelope(ordinal,[]))
                if overhead+item_bytes+size+len(items)>max_bytes:
                    if items:break
                    value=ref;size=encoded_size(value)
                    if overhead+size>max_bytes:
                        raise ValueError('page byte budget cannot hold reference')
                items.append(value);last=ordinal;item_bytes+=size
            return envelope(last,items)
        return self.store.read(read)

    def read_record(self,reference,*,offset=0,size=65536):
        if type(offset) is not int or offset<0 or type(size) is not int or size<1:
            raise ValueError('invalid result byte range')
        def read(view):
            self._identity(view,reference)
            ordinal=reference['ordinal']
            if type(ordinal) is not int or ordinal<0:raise ValueError('invalid record ordinal')
            rows=view.query('SELECT raw_bytes FROM result_rows WHERE set_id=? AND ordinal=?',(reference['id'],ordinal),max_rows=1)
            if not rows:raise KeyError(ordinal)
            total=rows[0][0];data=bytearray()
            for start,payload in view.iter_query('SELECT raw_start,payload FROM result_chunks WHERE set_id=? AND ordinal=? AND chunk>=? ORDER BY chunk',
                                                (reference['id'],ordinal,offset//65536)):
                raw=zlib.decompress(payload)
                data.extend(raw[max(0,offset-start):max(0,offset-start)+size-len(data)])
                if len(data)==size:break
            end=offset+len(data)
            return {'data':bytes(data),'total_bytes':total,'next_offset':end if end<total else None}
        return self.store.read(read)


def results_plugin(*,storage=('storage','store'),name='results'):
    from .plugins import Plugin
    def install(context):context.provide('results',Results(context.require(*storage)))
    return Plugin(name,(storage[0],),install,schema=RESULTS_SCHEMA)
