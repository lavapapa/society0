"""可选语义索引独占异步请求、SQL 向量及可重建的外部集合。"""
import asyncio
import json
import struct
from functools import wraps
from collections import OrderedDict
from ..kernel.vectors import validate_vectors
from .social_domain import DomainService


def _operation(method):
    @wraps(method)
    async def run(self,*args,**kwargs):
        self._check()
        task=asyncio.current_task()
        self._operations[task]=self._operations.get(task,0)+1
        try:return await method(self,*args,**kwargs)
        finally:
            depth=self._operations[task]-1
            if depth:self._operations[task]=depth
            else:self._operations.pop(task)
    return run

class Semantic(DomainService):
    def __init__(self,data,candidates,embed,client,*,cache_capacity=128):
        super().__init__(data)
        self.actors=data.actors
        self.candidates=candidates
        self.embed,self.client=embed,client
        vector_id=self.store.read(lambda r:r.query(f'SELECT vector_id FROM {self.table("config")} WHERE id=1')[0][0])
        self._collection_name='society-posts-'+self.store.run_id+'-'+vector_id
        self._collection=None
        self._embedding_lock=asyncio.Lock()
        if type(cache_capacity) is not int or cache_capacity<1:raise ValueError('cache_capacity must be a positive integer')
        self.cache_capacity=cache_capacity
        self._semantic_cache=OrderedDict()
        self._operations={};self._closed=False

    def enqueue_in(self,w,identifier,ordinal):
        w.execute(f'INSERT INTO {self.table("embedding_pending")} VALUES(?,?)',(identifier,ordinal))

    def _check(self):
        if self._closed:raise RuntimeError('Social is closed')

    async def close(self):
        self._closed=True
        tasks=set(self._operations)-{asyncio.current_task()}
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)

    @_operation
    async def flush_embeddings(self):
        if not self.config.social_media.recommendation.use_embedding_similarity:return
        if self.embed is None or self.client is None:raise ValueError('semantic recommendations require embedding and vector resources')
        async with self._embedding_lock:
            while True:
                rows=self.store.read(lambda r:r.query(f'SELECT p.id,p.ordinal,b.body,b.tags FROM {self.table("embedding_pending")} p JOIN {self.table("bodies")} b ON b.id=p.id ORDER BY p.ordinal LIMIT 256',max_rows=256))
                if not rows:break
                texts=[]
                for _,_,body,rawtags in rows:
                    tags=json.loads(rawtags)
                    texts.append(body.decode()+('\nTags: '+' '.join('#'+tag for tag in tags) if tags else ''))
                vectors=await self.embed(texts,metadata={'purpose':'social_posts','mechanism':self.name,'post_ids':[row[0] for row in rows]})
                dimensions=self.store.read(lambda r:r.query(f'SELECT dimension FROM {self.table("vectors")} LIMIT 1'))
                dimension=validate_vectors(vectors,len(rows),dimensions[0][0] if dimensions else None)
                def write(w):
                    for (identifier,ordinal,_,_),vector in zip(rows,vectors):
                        w.execute(f'INSERT INTO {self.table("vectors")} VALUES(?,?,?,?)',(identifier,ordinal,dimension,struct.pack('<'+str(dimension)+'d',*vector)))
                        w.execute(f'DELETE FROM {self.table("embedding_pending")} WHERE id=?',(identifier,))
                self.store.transaction(write)
            self._sync_vectors()

    def _sync_vectors(self):
        if self._collection is None:
            self._collection=self.client.get_or_create_collection(name=self._collection_name,metadata={'hnsw:space':'l2','through':0},embedding_function=None)
        after=(self._collection.metadata or {}).get('through',0)
        while True:
            rows=self.store.read(lambda r:r.query(f'SELECT id,ordinal,dimension,vector FROM {self.table("vectors")} WHERE ordinal>? ORDER BY ordinal LIMIT 256',(after,),max_rows=256))
            if not rows:break
            self._collection.upsert(ids=[row[0] for row in rows],
                embeddings=[list(struct.unpack('<'+str(row[2])+'d',row[3])) for row in rows],
                metadatas=[{'post_id':row[0],'mechanism':self.name} for row in rows])
            after=rows[-1][1]
            self._collection.modify(metadata={'through':after})

    @_operation
    async def similarities(self,actor,tick,*,query=None):
        cfg=self.config.social_media.recommendation
        scores={}
        if cfg.use_embedding_similarity:
            await self.flush_embeddings()
            pool=[item for item in self.candidates.active_pool(tick) if item['author_id']!=actor]
            dependencies=tuple(self.name+'_'+suffix for suffix in ('posts','bodies','edges','recent_interactions','members'))+('actor_personas',)
            source_version=self.store.read(lambda r:r.revision_for(dependencies))
            key=(query,source_version,tuple(item['post_id'] for item in pool),(self._collection.metadata or {}).get('through',0))
            previous=self._semantic_cache.get(actor)
            if previous is not None and previous[0]==key:
                scores=previous[1]
                self._semantic_cache.move_to_end(actor)
            elif pool:
                text=self.data.preference_text(actor) if query is None else query
                vectors=await self.embed([text],metadata={'purpose':'social_recommendation','actor':actor,'mechanism':self.name,'step':tick})
                # 网络等待期间可能新增帖子；补齐向量后再固定当前候选。
                await self.flush_embeddings()
                if self.store.read(lambda r:r.revision_for(dependencies))!=source_version:
                    raise ValueError('social preference or candidate revision changed during embedding')
                pool=[item for item in self.candidates.active_pool(tick) if item['author_id']!=actor]
                dimension=self.store.read(lambda r:r.query(f'SELECT dimension FROM {self.table("vectors")} LIMIT 1'))
                if dimension:validate_vectors(vectors,1,dimension[0][0])
                count=max(len(pool),cfg.post_count) if len(pool)<=cfg.full_scan_until else max(int(cfg.candidate_count*cfg.recall_multiplier),cfg.post_count)
                result=self._collection.query(query_embeddings=vectors,n_results=count,include=['distances'],where={'mechanism':{'$eq':self.name}})
                active={item['post_id'] for item in pool}
                scores={identifier:max(1-float(distance),0.0) for identifier,distance in zip(result['ids'][0],result['distances'][0]) if identifier in active}
                self._semantic_cache[actor]=(key,scores)
                self._semantic_cache.move_to_end(actor)
                if len(self._semantic_cache)>self.cache_capacity:self._semantic_cache.popitem(last=False)
        else:pool=self.candidates.active_pool(tick)
        return scores
