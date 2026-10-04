"""SQL 权威记忆与可重建的 Chroma 候选索引。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, asdict
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
import json
import itertools
import inspect
from functools import wraps
import math
import struct
import uuid

from ._json_chunks import decode_chunks

MEMORY_SCHEMA = (
    'CREATE TABLE memory_visibility(actor TEXT PRIMARY KEY NOT NULL,step INTEGER NOT NULL)',
    'CREATE TABLE memory_history(version_id TEXT PRIMARY KEY NOT NULL,id TEXT NOT NULL,actor TEXT NOT NULL,\n        type TEXT NOT NULL,timestamp INTEGER NOT NULL,importance REAL NOT NULL,visible_from INTEGER NOT NULL,\n        visible_until INTEGER NOT NULL,dimension INTEGER NOT NULL,vector BLOB NOT NULL)',
    'CREATE INDEX memory_history_visible ON memory_history(actor,visible_from,visible_until)',
    'CREATE TABLE memory_history_chunks(version_id TEXT NOT NULL REFERENCES memory_history(version_id),chunk INTEGER NOT NULL,raw_bytes INTEGER NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(version_id,chunk))',

    '''CREATE TABLE memory_state(id INTEGER PRIMARY KEY,revision INTEGER NOT NULL,dimension INTEGER)''',
    '''CREATE TABLE memory_rows(id TEXT PRIMARY KEY NOT NULL,actor TEXT NOT NULL,type TEXT NOT NULL,
        timestamp INTEGER NOT NULL,importance REAL NOT NULL,state TEXT NOT NULL,revision INTEGER NOT NULL,visible_step INTEGER NOT NULL)''',
    'CREATE INDEX memory_actor_ready ON memory_rows(actor,state,timestamp,id)',
    'CREATE INDEX memory_revision ON memory_rows(revision)',
    '''CREATE TABLE memory_chunks(id TEXT NOT NULL REFERENCES memory_rows(id) ON DELETE CASCADE,
        chunk INTEGER NOT NULL,raw_bytes INTEGER NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(id,chunk))''',
    '''CREATE TABLE memory_vectors(id TEXT PRIMARY KEY NOT NULL REFERENCES memory_rows(id) ON DELETE CASCADE,
        dimension INTEGER NOT NULL,vector BLOB NOT NULL)''',
    '''CREATE TABLE memory_jobs(id TEXT PRIMARY KEY NOT NULL,actor TEXT NOT NULL,thread_id TEXT,
        job_key TEXT NOT NULL,timestamp INTEGER NOT NULL,state TEXT NOT NULL,visible_step INTEGER NOT NULL,UNIQUE(actor,job_key))''',
    'CREATE INDEX memory_pending ON memory_jobs(actor,state,id)',
    '''CREATE TABLE memory_job_items(job_id TEXT NOT NULL REFERENCES memory_jobs(id),ordinal INTEGER NOT NULL,
        memory_id TEXT NOT NULL REFERENCES memory_rows(id),PRIMARY KEY(job_id,ordinal))''',
)


def _write_body(writer, identifier, item):
    index = 0
    def emit(size, payload):
        nonlocal index
        writer.execute('INSERT INTO memory_chunks VALUES(?,?,?,?)', (identifier,index,size,payload))
        index += 1
    writer.write_json_chunks({'content':item['content'],'metadata':item['metadata']},emit)


@dataclass(frozen=True)
class MemoryPolicy:
    auto_recall: bool = True
    auto_write: bool = True
    active_tools: bool = True


@dataclass(frozen=True)
class MemoryActivation:
    policy: MemoryPolicy
    recall_top_k: int = 10

    def __post_init__(self):
        if not isinstance(self.policy,MemoryPolicy):
            raise TypeError('activation policy must be MemoryPolicy')
        if type(self.recall_top_k) is not int or self.recall_top_k < 1:
            raise ValueError('recall_top_k must be positive')


@dataclass
class _Binding:
    scope: object
    selection: MemoryActivation
    step: int
    active: bool = True


def _payload(view, memory_id):
    rows = view.iter_query('SELECT payload FROM memory_chunks WHERE id=? ORDER BY chunk',(memory_id,))
    return decode_chunks(payload for (payload,) in rows)


def _revision(view):
    rows=view.query('SELECT revision FROM memory_state WHERE id=1')
    return rows[0][0] if rows else 0


def _next_revision(writer):
    writer.execute('INSERT INTO memory_state VALUES(1,1,NULL) ON CONFLICT(id) DO UPDATE SET revision=revision+1')
    return _revision(writer)


def _visible(writer,actor,step):
    rows=writer.query('SELECT step FROM memory_visibility WHERE actor=?',(actor,))
    if rows and step<rows[0][0]:raise ValueError('memory visibility cannot move backwards')
    writer.execute('INSERT INTO memory_visibility VALUES(?,?) ON CONFLICT(actor) DO UPDATE SET step=excluded.step',(actor,step))


def _archive(writer,identifier,step):
    rows=writer.query('SELECT visible_step FROM memory_rows WHERE id=?',(identifier,))
    if step<=rows[0][0]:return
    version=uuid.uuid4().hex
    writer.execute('INSERT INTO memory_history SELECT ?,m.id,m.actor,m.type,m.timestamp,m.importance,m.visible_step,?,v.dimension,v.vector FROM memory_rows m JOIN memory_vectors v ON v.id=m.id WHERE m.id=?',
                   (version,step,identifier))
    writer.execute('INSERT INTO memory_history_chunks SELECT ?,chunk,raw_bytes,payload FROM memory_chunks WHERE id=?',(version,identifier))


def _entry(value):
    content=value['content']
    kind=value.get('type','episodic')
    importance=value.get('importance',3.0)
    if not isinstance(content,str) or not content.strip() or kind not in ('episodic','semantic'):
        raise ValueError('invalid memory content or type')
    if type(importance) not in (int,float) or not math.isfinite(importance) or not 0<=importance<=5:
        raise ValueError('memory importance must be finite and between 0 and 5')
    return {'content':content,'type':kind,'importance':float(importance),'metadata':value.get('metadata',{})}


def _vectors(values, count, dimension=None):
    if len(values)!=count:
        raise ValueError('embedding result count differs from input')
    expected=dimension
    for vector in values:
        if not vector or any(type(value) not in (int,float) or not math.isfinite(value) for value in vector):
            raise ValueError('invalid embedding vector')
        expected=len(vector) if expected is None else expected
        if len(vector)!=expected:
            raise ValueError('embedding dimension differs')
    return expected


@contextmanager
def _operation_scope(memory):
    memory._check()
    task=asyncio.current_task()
    memory._operations[task]=memory._operations.get(task,0)+1
    try:yield
    finally:
        depth=memory._operations[task]-1
        if depth:memory._operations[task]=depth
        else:memory._operations.pop(task)


def _operation(method):
    @wraps(method)
    async def scoped(self,*args,**kwargs):
        with _operation_scope(self):
            return await method(self,*args,**kwargs)
    return scoped


class Memory:
    def __init__(self,store,threads,*,embed,client,extract=None,policy=None,recall_query=None,decay_rate=0.01,recall_top_k=10,policy_selector=None):
        self.store,self.threads=store,threads
        self.embed,self.client,self.extract=embed,client,extract
        self.policy=policy or MemoryPolicy()
        self.recall_query=recall_query
        if type(recall_top_k) is not int or recall_top_k<1:raise ValueError('recall_top_k must be positive')
        self.recall_top_k=recall_top_k
        self.decay_rate=decay_rate
        self._collection=None
        self._flights={}
        self._closed=False
        self._operations={}
        self.policy_selector=policy_selector
        self._activation=ContextVar("memory_activation",default=None)

    def _check(self):
        if self._closed:raise RuntimeError('Memory is closed')

    def _selected(self, *, scope=None, session=None):
        self._check()
        binding=self._activation.get()
        if binding is None:
            return MemoryActivation(self.policy,self.recall_top_k)
        if not binding.active:
            raise RuntimeError('memory activation is closed')
        actual=scope if session is None else session.scope
        if actual is not None and actual is not binding.scope:
            raise RuntimeError('memory activation belongs to another scope')
        binding.scope.check_active()
        return binding.selection

    def _step(self, *, session=None, scope=None):
        self._selected(session=session,scope=scope)
        binding=self._activation.get()
        if binding is not None:return binding.step
        if session is not None:return session.step
        raise RuntimeError('memory action requires an activation scope')

    @asynccontextmanager
    async def activation(self,session,thread_id):
        from ..async_utils import invoke_maybe_async
        with _operation_scope(self):
            session.scope.check_active()
            selection=(MemoryActivation(self.policy,self.recall_top_k) if self.policy_selector is None
                       else await invoke_maybe_async(self.policy_selector,session))
            if not isinstance(selection,MemoryActivation):
                raise TypeError('memory policy selector must return MemoryActivation')
            session.scope.check_active()
            head=self.threads.describe(thread_id)
            if head['actor']!=session.actor.id:
                raise PermissionError('memory Thread owner differs')
            if selection.policy.auto_write and head['kind']!='interview' and self.extract is None:
                raise ValueError('automatic memory write requires an extractor')
            if selection.policy.auto_recall and self.recall_query is None:
                raise ValueError('automatic memory recall requires recall_query')
            self.threads.event(thread_id,'memory_policy',{
                **asdict(selection.policy),'recall_top_k':selection.recall_top_k,
                'effective_auto_write':selection.policy.auto_write and head['kind']!='interview','step':session.step})
            binding=_Binding(session.scope,selection,session.step)
            token=self._activation.set(binding)
            try:yield self
            finally:
                binding.active=False
                self._activation.reset(token)

    async def close(self):
        self._closed=True
        tasks=(set(self._flights.values())|set(self._operations))-{asyncio.current_task()}
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        self._flights.clear()

    @_operation
    async def seed(self,actor,job_key,*,timestamp,entries,visible_step=None):
        self._check()
        job=self.prepare_job(actor,None,'seed:'+job_key,timestamp=timestamp,entries=entries,visible_step=visible_step)
        return await self.finish_job(job)

    def export(self,actor,consume):
        def read(view):
            count=0
            for identifier,kind,timestamp,importance in view.iter_query("SELECT id,type,timestamp,importance FROM memory_rows WHERE actor=? AND state='ready' ORDER BY id",(actor,)):
                item=_payload(view,identifier)
                dimension,raw=view.query('SELECT dimension,vector FROM memory_vectors WHERE id=?',(identifier,))[0]
                item.update(id=identifier,type=kind,timestamp=timestamp,importance=importance,
                            embedding=list(struct.unpack('<'+str(dimension)+'d',raw)))
                result=consume(item)
                if inspect.isawaitable(result):
                    if inspect.iscoroutine(result):result.close()
                    raise TypeError('memory export consumer must be synchronous')
                count+=1
            return count
        return self.store.read(read)

    def import_records(self,actor,records,*,visible_step):
        self._check()
        if type(visible_step) is not int:raise ValueError('memory visible_step must be an integer')
        def write(writer):
            rows=writer.query('SELECT dimension FROM memory_state WHERE id=1')
            dimension=rows[0][0] if rows else None
            count=0
            for record in records:
                item=_entry(record)
                timestamp=record['timestamp']
                if type(timestamp) is not int:raise ValueError('memory timestamp must be an integer')
                vector=record['embedding']
                dimension=_vectors([vector],1,dimension)
                identifier=record.get('id') or uuid.uuid4().hex
                if count==0:_visible(writer,actor,visible_step)
                revision=_next_revision(writer)
                writer.execute('UPDATE memory_state SET dimension=? WHERE id=1',(dimension,))
                writer.execute('INSERT INTO memory_rows VALUES(?,?,?,?,?,?,?,?)',
                    (identifier,actor,item['type'],timestamp,item['importance'],'ready',revision,visible_step))
                _write_body(writer,identifier,item)
                writer.execute('INSERT INTO memory_vectors VALUES(?,?,?)',(identifier,dimension,struct.pack('<'+str(dimension)+'d',*vector)))
                count+=1
            return count
        return self.store.transaction(write)

    def job(self,job_id):
        def read(view):
            rows=view.query('SELECT actor,thread_id,job_key,timestamp,state,visible_step FROM memory_jobs WHERE id=?',(job_id,))
            if not rows:raise KeyError(job_id)
            result=dict(zip(('actor','thread_id','job_key','timestamp','state','visible_step'),rows[0]),id=job_id)
            result['memory_ids']=[row[0] for row in view.iter_query('SELECT memory_id FROM memory_job_items WHERE job_id=? ORDER BY ordinal',(job_id,))]
            return result
        return self.store.read(read)

    def get(self,memory_id,*,actor):
        def read(view):
            rows=view.query('SELECT actor,type,timestamp,importance,state FROM memory_rows WHERE id=?',(memory_id,))
            if not rows:raise KeyError(memory_id)
            owner,kind,timestamp,importance,state=rows[0]
            if owner!=actor:raise PermissionError('memory owner differs')
            if state=='deleted':raise KeyError(memory_id)
            result=_payload(view,memory_id)
            vector=view.query('SELECT dimension,vector FROM memory_vectors WHERE id=?',(memory_id,))
            result.update(id=memory_id,actor=owner,type=kind,timestamp=timestamp,importance=importance,state=state,
                          embedding=list(struct.unpack('<'+str(vector[0][0])+'d',vector[0][1])) if vector else None)
            return result
        return self.store.read(read)

    def prepare_job(self,actor,thread_id,job_key,*,timestamp,entries,visible_step=None):
        self._check()
        visible_step=timestamp if visible_step is None else visible_step
        if type(visible_step) is not int:raise ValueError('memory visible_step must be an integer')
        if type(timestamp) is not int:raise ValueError('memory timestamp must be an integer')
        entries=[_entry(item) for item in entries]
        existing=self.store.read(lambda view:view.query('SELECT id FROM memory_jobs WHERE actor=? AND job_key=?',(actor,job_key)))
        if existing:
            previous=self.job(existing[0][0])
            previous_entries=[]
            if previous['state']=='prepared':
                for identifier in previous['memory_ids']:
                    item=self.get(identifier,actor=actor)
                    previous_entries.append({key:item[key] for key in ('content','type','importance','metadata')})
            if previous['thread_id']!=thread_id or previous['timestamp']!=timestamp or previous['visible_step']!=visible_step or (previous['state']=='prepared' and previous_entries!=entries):
                raise ValueError('memory job identity reused with different input')
            return previous['id']
        job_id=uuid.uuid4().hex
        def write(writer):
            if thread_id is not None:
                rows=writer.query('SELECT actor FROM thread_heads WHERE id=?',(thread_id,))
                if not rows or rows[0][0]!=actor:raise PermissionError('Thread owner differs')
            writer.execute('INSERT INTO memory_jobs VALUES(?,?,?,?,?,?,?)',(job_id,actor,thread_id,job_key,timestamp,'prepared',visible_step))
            for ordinal,item in enumerate(entries):
                memory_id=uuid.uuid4().hex
                writer.execute('INSERT INTO memory_rows VALUES(?,?,?,?,?,?,0,?)',(memory_id,actor,item['type'],timestamp,item['importance'],'pending',visible_step))
                _write_body(writer,memory_id,item)
                writer.execute('INSERT INTO memory_job_items VALUES(?,?,?)',(job_id,ordinal,memory_id))
        self.store.transaction(write)
        return job_id

    @_operation
    async def finish_job(self,job_id):
        self._check()
        if job_id in self._flights:
            return await asyncio.shield(self._flights[job_id])
        task=asyncio.create_task(self._finish_job(job_id))
        self._flights[job_id]=task
        try:return await task
        finally:self._flights.pop(job_id,None)

    async def _finish_job(self,job_id):
        job=self.job(job_id)
        if job['state']=='complete':return job['memory_ids']
        if job['state']=='prepared':
            records=[self.get(identifier,actor=job['actor']) for identifier in job['memory_ids']]
            texts=[record['content'] for record in records]
            vectors=await self.embed(texts,metadata={'actor':job['actor'],'thread_id':job['thread_id'],
                                                    'job_id':job_id,'memory_ids':job['memory_ids'],'purpose':'memory_write'}) if texts else []
            self._check()
            rows=self.store.read(lambda view:view.query('SELECT dimension FROM memory_state WHERE id=1'))
            dimension=_vectors(vectors,len(texts),rows[0][0] if rows else None)
            def write(writer):
                _visible(writer,job['actor'],job['visible_step'])
                for identifier,vector in zip(job['memory_ids'],vectors):
                    revision=_next_revision(writer)
                    writer.execute('INSERT INTO memory_vectors VALUES(?,?,?)',(identifier,len(vector),struct.pack('<'+str(len(vector))+'d',*vector)))
                    writer.execute("UPDATE memory_rows SET state='ready',revision=? WHERE id=?",(revision,identifier))
                if dimension is not None:
                    writer.execute('UPDATE memory_state SET dimension=? WHERE id=1',(dimension,))
                writer.execute("UPDATE memory_jobs SET state='written' WHERE id=?",(job_id,))
            self.store.transaction(write)
        if job['memory_ids']:
            await self.sync_index()
        self.store.transaction(lambda writer:writer.execute("UPDATE memory_jobs SET state='complete' WHERE id=?",(job_id,)))
        return job['memory_ids']

    @_operation
    async def update(self,memory_id,*,actor,content,timestamp,importance=None,metadata=None,visible_step=None):
        self._check()
        visible_step=timestamp if visible_step is None else visible_step
        if type(visible_step) is not int:raise ValueError('memory visible_step must be an integer')
        if type(timestamp) is not int:raise ValueError('memory timestamp must be an integer')
        old=self.get(memory_id,actor=actor)
        if old['state']!='ready':raise ValueError('pending memory cannot be edited')
        revision=self.store.read(lambda view:view.query('SELECT revision FROM memory_rows WHERE id=?',(memory_id,)))[0][0]
        item=_entry({'content':content,'type':old['type'],'importance':3.0 if importance is None else importance,
                     'metadata':{} if metadata is None else metadata})
        vectors=await self.embed([content],metadata={'actor':actor,'memory_ids':[memory_id],'purpose':'memory_update'})
        self._check()
        dimension=self.store.read(lambda view:view.query('SELECT dimension FROM memory_state WHERE id=1'))[0][0]
        _vectors(vectors,1,dimension)
        def write(writer):
            row=writer.query('SELECT revision,state FROM memory_rows WHERE id=?',(memory_id,))[0]
            if row!=(revision,'ready'):raise RuntimeError('memory changed during embedding')
            _visible(writer,actor,visible_step)
            _archive(writer,memory_id,visible_step)
            next_revision=_next_revision(writer)
            writer.execute('DELETE FROM memory_chunks WHERE id=?',(memory_id,))
            _write_body(writer,memory_id,item)
            writer.execute('UPDATE memory_vectors SET vector=? WHERE id=?',(struct.pack('<'+str(dimension)+'d',*vectors[0]),memory_id))
            writer.execute('UPDATE memory_rows SET timestamp=?,importance=?,revision=?,visible_step=? WHERE id=?',
                           (timestamp,item['importance'],next_revision,visible_step,memory_id))
        self.store.transaction(write)
        await self.sync_index()
        return memory_id

    @_operation
    async def delete(self,memory_id,*,actor,visible_step=None):
        self._check()
        def write(writer):
            rows=writer.query('SELECT actor,state FROM memory_rows WHERE id=?',(memory_id,))
            if not rows:raise KeyError(memory_id)
            owner,state=rows[0]
            if owner!=actor:raise PermissionError('memory owner differs')
            if state=='pending':raise ValueError('pending memory cannot be deleted')
            if state=='deleted':return
            rows=writer.query('SELECT step FROM memory_visibility WHERE actor=?',(actor,))
            step=rows[0][0] if visible_step is None else visible_step
            if type(step) is not int:raise ValueError('memory visible_step must be an integer')
            _visible(writer,actor,step)
            _archive(writer,memory_id,step)
            revision=_next_revision(writer)
            writer.execute("UPDATE memory_rows SET state='deleted',revision=? WHERE id=?",(revision,memory_id))
            writer.execute('DELETE FROM memory_chunks WHERE id=?',(memory_id,))
            writer.execute('DELETE FROM memory_vectors WHERE id=?',(memory_id,))
        self.store.transaction(write)
        await self.sync_index()
        return memory_id

    @_operation
    async def sync_index(self):
        self._check()
        if self._collection is None:
            self._collection=self.client.get_or_create_collection(name='society-memory-'+self.store.run_id,
                metadata={'source_run_id':self.store.run_id,'revision':-1,'hnsw:space':'l2'},embedding_function=None)
        metadata=dict(self._collection.metadata or {})
        target=self.store.read(_revision)
        watermark=metadata.get('revision',-1)
        if metadata.get('source_run_id')!=self.store.run_id or watermark>target:
            self.client.delete_collection(name='society-memory-'+self.store.run_id)
            self._collection=None
            return await self.sync_index()
        after=watermark
        while after<target or after==-1:
            def read(view):
                return view.query('''SELECT m.id,m.actor,m.type,m.timestamp,m.importance,m.state,m.revision,v.dimension,v.vector,m.visible_step
                    FROM memory_rows m LEFT JOIN memory_vectors v ON v.id=m.id
                    WHERE m.revision>? AND m.revision<=? ORDER BY m.revision,m.id LIMIT 256''',(after,target),max_rows=256)
            rows=self.store.read(read)
            if not rows:break
            ready=[row for row in rows if row[5]=='ready']
            deleted=[row[0] for row in rows if row[5]=='deleted']
            if ready:
                self._collection.upsert(ids=[row[0] for row in ready],
                    embeddings=[list(struct.unpack('<'+str(row[7])+'d',row[8])) for row in ready],
                    metadatas=[{'actor':row[1],'type':row[2],'timestamp':row[3],'importance':row[4],'visible_step':row[9]} for row in ready])
            if deleted:self._collection.delete(ids=deleted)
            after=rows[-1][6]
        metadata.pop('hnsw:space',None)
        metadata.update(source_run_id=self.store.run_id,revision=target)
        self._collection.modify(metadata=metadata)

    @_operation
    async def recall(self,actor,query,*,top_k=10,current_step=None,thread_id=None):
        self._check()
        if type(top_k) is not int or top_k<1:raise ValueError('top_k must be positive')
        await self.finish_pending(actor)
        watermark=self.store.read(lambda view:view.query('SELECT step FROM memory_visibility WHERE actor=?',(actor,)))
        if current_step is not None and watermark and current_step<watermark[0][0]:
            return await self._recall_history(actor,query,top_k,current_step,thread_id)
        clause="actor=? AND state='ready'"
        values=[actor]
        if current_step is not None:
            clause+=' AND visible_step<=?'
            values.append(current_step)
        if not self.store.read(lambda view:view.query('SELECT 1 FROM memory_rows WHERE '+clause+' LIMIT 1',values)):
            return []
        vectors=await self.embed([query],metadata={'actor':actor,'thread_id':thread_id,'purpose':'memory_recall'})
        dimension=self.store.read(lambda view:view.query('SELECT dimension FROM memory_state WHERE id=1'))[0][0]
        _vectors(vectors,1,dimension)
        watermark=self.store.read(lambda view:view.query('SELECT step FROM memory_visibility WHERE actor=?',(actor,)))
        if current_step is not None and watermark and current_step<watermark[0][0]:
            return await self._recall_history(actor,query,top_k,current_step,thread_id,query_vectors=vectors)
        await self.sync_index()
        where={'actor':{'$eq':actor}}
        if current_step is not None:where={'$and':[where,{'visible_step':{'$lte':current_step}}]}
        results=self._collection.query(query_embeddings=vectors,n_results=top_k*2,where=where,include=['distances'])
        candidates={}
        for identifier,distance in zip(results['ids'][0],results['distances'][0]):
            item=self.get(identifier,actor=actor)
            relevance=item['importance']*(math.exp(-self.decay_rate*max(0,current_step-item['timestamp'])) if current_step is not None else 1)
            score=max(-1.0,min(1.0,1-float(distance)))+relevance*0.1
            key=item['type'],item['content']
            if key not in candidates or score>candidates[key]['score']:
                candidates[key]=dict(item,score=score)
        return sorted(candidates.values(),key=lambda item:item['score'],reverse=True)[:top_k]

    async def _recall_history(self,actor,query,top_k,step,thread_id,query_vectors=None):
        dimensions=self.store.read(lambda view:view.query('SELECT dimension FROM memory_state WHERE id=1'))
        if not dimensions or dimensions[0][0] is None:return []
        if query_vectors is None:
            query_vectors=await self.embed([query],metadata={'actor':actor,'thread_id':thread_id,'purpose':'memory_recall'})
        self._check()
        dimension=self.store.read(lambda view:view.query('SELECT dimension FROM memory_state WHERE id=1'))[0][0]
        _vectors(query_vectors,1,dimension)
        name='society-history-'+uuid.uuid4().hex
        collection=self.client.get_or_create_collection(name=name,metadata={'hnsw:space':'l2'},embedding_function=None)
        try:
            # 历史查询显式重建临时候选索引；当前查询始终走独立当前投影。
            def build(view):
                records=view.iter_query("SELECT 'c:'||id FROM memory_rows WHERE actor=? AND state='ready' AND visible_step<=? UNION ALL SELECT 'h:'||version_id FROM memory_history WHERE actor=? AND visible_from<=? AND visible_until>?",(actor,step,actor,step,step))
                count=0
                while batch:=list(itertools.islice(records,256)):
                    vectors=[]
                    for (key,) in batch:
                        table,column=('memory_vectors','id') if key.startswith('c:') else ('memory_history','version_id')
                        dimension,raw=view.query('SELECT dimension,vector FROM '+table+' WHERE '+column+'=?',(key[2:],))[0]
                        vectors.append(list(struct.unpack('<'+str(dimension)+'d',raw)))
                    collection.upsert(ids=[r[0] for r in batch],embeddings=vectors,metadatas=[{'actor':actor} for r in batch])
                    count+=len(batch)
                return count
            if not self.store.read(build):return []
            results=collection.query(query_embeddings=query_vectors,n_results=top_k*2,where={'actor':{'$eq':actor}},include=['distances'])
            candidates={}
            for key,distance in zip(results['ids'][0],results['distances'][0]):
                if key.startswith('c:'):item=self.get(key[2:],actor=actor)
                else:
                    def read(view):
                        row=view.query('SELECT id,type,timestamp,importance,dimension,vector FROM memory_history WHERE version_id=?',(key[2:],))[0]
                        item=decode_chunks(p for (p,) in view.iter_query('SELECT payload FROM memory_history_chunks WHERE version_id=? ORDER BY chunk',(key[2:],)))
                        item.update(id=row[0],actor=actor,type=row[1],timestamp=row[2],importance=row[3],state='ready',embedding=list(struct.unpack('<'+str(row[4])+'d',row[5])))
                        return item
                    item=self.store.read(read)
                score=max(-1.0,min(1.0,1-float(distance)))+0.1*item['importance']*math.exp(-self.decay_rate*max(0,step-item['timestamp']))
                identity=item['type'],item['content']
                if identity not in candidates or score>candidates[identity]['score']:candidates[identity]=dict(item,score=score)
            return sorted(candidates.values(),key=lambda item:item['score'],reverse=True)[:top_k]
        finally:self.client.delete_collection(name=name)

    @_operation
    async def finish_pending(self,actor):
        while True:
            rows=self.store.read(lambda view:view.query("SELECT id FROM memory_jobs WHERE actor=? AND state IN ('prepared','written') ORDER BY id LIMIT 100",(actor,),max_rows=100))
            if not rows:return
            await asyncio.gather(*(self.finish_job(job_id) for (job_id,) in rows))

    @_operation
    async def extract_job(self,actor,thread_id,*,through,timestamp):
        self._check()
        key='thread:'+thread_id+':through:'+str(through)
        existing=self.store.read(lambda view:view.query('SELECT id FROM memory_jobs WHERE actor=? AND job_key=?',(actor,key)))
        if existing:return existing[0][0]
        flight=('extract',actor,key)
        if flight in self._flights:return await asyncio.shield(self._flights[flight])
        async def prepare():
            if self.extract is None:raise ValueError('memory extraction is not configured')
            snapshot=self.threads.snapshot_messages(thread_id,through=through)
            entries=await self.extract(actor,thread_id,snapshot['messages'],metadata={'through':through,'job_key':key})
            return self.prepare_job(actor,thread_id,key,timestamp=timestamp,entries=entries)
        task=asyncio.create_task(prepare())
        self._flights[flight]=task
        try:return await task
        finally:self._flights.pop(flight,None)

    @_operation
    async def before_activation(self,session,thread_id):
        self._check()
        from ..async_utils import invoke_maybe_async
        selection=self._selected(session=session)
        messages=[]
        if selection.policy.active_tools:
            messages.append({'role':'user','content':json.dumps({'memory_actions_target':{'namespace':'memory','kind':'actor','key':session.actor.id}},ensure_ascii=False)})
        if selection.policy.auto_recall:
            if self.recall_query is None:raise ValueError('automatic memory recall requires recall_query')
            query=await invoke_maybe_async(self.recall_query,session)
            hits=await self.recall(session.actor.id,query,top_k=selection.recall_top_k,current_step=self._step(session=session),thread_id=thread_id)
            if hits:
                messages.append({'role':'user','content':json.dumps({'recalled_memories':[item['content'] for item in hits]},ensure_ascii=False)})
        return messages

    @_operation
    async def after_activation(self,session,thread_id,result):
        self._check()
        if not self._selected(session=session).policy.auto_write or result.status not in ('completed','waiting'):return
        if self.threads.describe(thread_id)['kind']=='interview':return
        through=result.value['memory_input_through']
        job=await self.extract_job(session.actor.id,thread_id,through=through,timestamp=self._step(session=session))
        await self.finish_job(job)

    def actions(self):
        from .interaction import Action,ActionResult
        async def remember(scope,target,arguments):
            thread=self.threads.find(scope.actor,scope.moment)
            job=self.prepare_job(scope.actor,thread,uuid.uuid4().hex,timestamp=self._step(scope=scope),entries=[arguments])
            ids=await self.finish_job(job)
            return ActionResult('completed',{'memory_ids':ids})
        async def recall(scope,target,arguments):
            thread=self.threads.find(scope.actor,scope.moment)
            hits=await self.recall(scope.actor,arguments['query'],top_k=arguments.get('top_k',10),current_step=self._step(scope=scope),thread_id=thread)
            return ActionResult('completed',{'memories':[{'id':item['id'],'type':item['type'],'content':item['content'],'score':item['score']} for item in hits]})
        async def update(scope,target,arguments):
            identifier=arguments['memory_id']
            try:self.get(identifier,actor=scope.actor)
            except (KeyError,PermissionError):return ActionResult('rejected',{'reason':'memory_unavailable'})
            await self.update(identifier,actor=scope.actor,content=arguments['content'],timestamp=self._step(scope=scope),
                              visible_step=self._step(scope=scope),importance=arguments.get('importance'))
            return ActionResult('completed',{'memory_id':identifier})
        async def delete(scope,target,arguments):
            identifier=arguments['memory_id']
            try:self.get(identifier,actor=scope.actor)
            except (KeyError,PermissionError):return ActionResult('rejected',{'reason':'memory_unavailable'})
            await self.delete(identifier,actor=scope.actor,visible_step=self._step(scope=scope))
            return ActionResult('completed',{'memory_id':identifier})
        def available(scope,target):return self._activation.get() is not None and self._selected(scope=scope).policy.active_tools and scope.actor==target.key
        return (
            Action('memory.update',('memory','actor'),'修改该主体已经保存的记忆',
                {'type':'object','properties':{'memory_id':{'type':'string'},'content':{'type':'string','minLength':1},'importance':{'type':'number','minimum':0,'maximum':5}},'required':['memory_id','content'],'additionalProperties':False},
                update,available=available),
            Action('memory.delete',('memory','actor'),'删除该主体当前可见的记忆，保留既往时点版本',
                {'type':'object','properties':{'memory_id':{'type':'string'}},'required':['memory_id'],'additionalProperties':False},
                delete,available=available),
            Action('memory.remember',('memory','actor'),'保存主体明确选择的记忆',
                {'type':'object','properties':{'content':{'type':'string','minLength':1},'importance':{'type':'number','minimum':0,'maximum':5},'type':{'enum':['episodic','semantic']}},'required':['content'],'additionalProperties':False},
                remember,available=available),
            Action('memory.recall',('memory','actor'),'检索该主体的相关记忆',
                {'type':'object','properties':{'query':{'type':'string'},'top_k':{'type':'integer','minimum':1}},'required':['query'],'additionalProperties':False},
                recall,available=available,read_only=True),
        )


class ThreadMemoryExtractor:
    """在原 Thread 上追加提取回合，复用标准模型提供方的物理留证。"""
    def __init__(self,threads,provider,*,request_options=None):
        self.threads,self.provider=threads,provider
        self.request_options = {"max_tokens":4096} if request_options is None else dict(request_options)
        if hasattr(provider,"validate_options"): provider.validate_options(self.request_options)

    async def __call__(self,actor,thread_id,messages,*,metadata):
        from ..memory_extraction_protocol import _extraction_prompt,_extraction_tool,_parse_memories_from_response
        if self.threads.describe(thread_id)['actor']!=actor:raise PermissionError('Thread owner differs')
        if not messages or messages[0].get('role')!='system':raise ValueError('memory extraction requires original system context')
        self.threads.append_message(thread_id,{'role':'user','content':_extraction_prompt()})
        for attempt in range(2):
            response=await self.provider.request(thread_id,{'tools':[_extraction_tool()],
                'tool_choice':{'type':'function','function':{'name':'extract_memories'}},
                'parallel_tool_calls':False,**self.request_options})
            if response.get('message_seq') is None:
                self.threads.append_message(thread_id,{key:value for key,value in response.items() if key!='finish_reason'})
            if response.get('incomplete_reason') or response.get('finish_reason')=='length':
                raise RuntimeError('memory extraction incomplete: '+response.get('incomplete_reason','length'))
            entries,call_id,error=_parse_memories_from_response(response)
            for call in response.get('tool_calls') or []:
                if isinstance(call,dict) and call.get('id'):
                    self.threads.append_message(thread_id,{'role':'tool','tool_call_id':call['id'],
                        'content':json.dumps({'accepted':entries is not None,'error':error or None},ensure_ascii=False)})
            if entries is not None:return entries
            if attempt==0:
                self.threads.append_message(thread_id,{'role':'user','content':
                    '提取工具调用未通过校验（'+error+'）。请重新调用 extract_memories；memories 必须直接为数组，每项含 content 和 importance。没有值得保留的记忆时返回空数组。'})
        raise RuntimeError('memory extraction failed: '+error)
