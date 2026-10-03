"""共享社交机制：不可变正文、当前计数与关系索引。"""
import json
import math
import asyncio
import struct
import uuid
import codecs
from functools import wraps

from ..kernel.plugins import Plugin
from ..kernel.interaction import Action, ActionResult, Page, Ref, Unavailable
from dataclasses import asdict
from ..kernel.information_sql import SQLInformation, DatasetSpec, DocumentSpec, _quote
from .social_models import SocialNetworkConfig
from .social_topology import generate_topology
from ..kernel.memory import _vectors


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


def _schema(name):
    t=lambda suffix:_quote(name+'_'+suffix)
    return (
        f'CREATE TABLE {t("config")}(id INTEGER PRIMARY KEY,body TEXT NOT NULL,vector_id TEXT NOT NULL)',
        f'CREATE TABLE {t("head")}(id INTEGER PRIMARY KEY,post_count INTEGER NOT NULL,content_limit INTEGER NOT NULL,member_count INTEGER NOT NULL)',
        f'CREATE TABLE {t("members")}(id TEXT PRIMARY KEY NOT NULL,ordinal INTEGER NOT NULL,post_count INTEGER NOT NULL,followers INTEGER NOT NULL,following INTEGER NOT NULL,unread INTEGER NOT NULL,notice_count INTEGER NOT NULL,FOREIGN KEY(id) REFERENCES actors(id))',
        f'CREATE TABLE {t("edges")}(id INTEGER PRIMARY KEY,follower TEXT NOT NULL,followee TEXT NOT NULL,UNIQUE(follower,followee))',
        f'CREATE INDEX {t("edge_followee")} ON {t("edges")}(followee,id)',
        f'CREATE INDEX {t("edge_follower")} ON {t("edges")}(follower,id)',
        f'CREATE TABLE {t("posts")}(id TEXT PRIMARY KEY NOT NULL,ordinal INTEGER NOT NULL UNIQUE,author TEXT NOT NULL,created_tick INTEGER NOT NULL,reply_to TEXT,like_count INTEGER NOT NULL,reply_count INTEGER NOT NULL,repost_count INTEGER NOT NULL,view_count INTEGER NOT NULL,engagement REAL NOT NULL)',
        f'CREATE INDEX {t("author_posts")} ON {t("posts")}(author,ordinal)',
        f'CREATE INDEX {t("recent_posts")} ON {t("posts")}(created_tick,ordinal)',
        f'CREATE INDEX {t("pool_recent")} ON {t("posts")}(created_tick,id)',
        f'CREATE INDEX {t("pool_engagement")} ON {t("posts")}(engagement,created_tick,id)',
        f'CREATE TABLE {t("bodies")}(id TEXT PRIMARY KEY NOT NULL,body BLOB NOT NULL,tags TEXT NOT NULL,FOREIGN KEY(id) REFERENCES {t("posts")}(id))',
        f'CREATE TABLE {t("likes")}(id INTEGER PRIMARY KEY,post TEXT NOT NULL,actor TEXT NOT NULL,tick INTEGER NOT NULL,UNIQUE(post,actor))',
        f'CREATE INDEX {t("post_likes")} ON {t("likes")}(post,id)',
        f'CREATE TABLE {t("replies")}(id INTEGER PRIMARY KEY,post TEXT NOT NULL,author TEXT NOT NULL,tick INTEGER NOT NULL,body BLOB NOT NULL)',
        f'CREATE INDEX {t("post_replies")} ON {t("replies")}(post,id)',
        f'CREATE TABLE {t("notices")}(id INTEGER PRIMARY KEY,actor TEXT NOT NULL,type TEXT NOT NULL,tick INTEGER NOT NULL,consumed INTEGER NOT NULL)',
        f'CREATE INDEX {t("unread")} ON {t("notices")}(actor,consumed,id)',
        f'CREATE INDEX {t("notice_history")} ON {t("notices")}(actor,id)',
        f'CREATE TABLE {t("notice_data")}(id INTEGER PRIMARY KEY,data BLOB NOT NULL)',
        f'CREATE TABLE {t("events")}(id INTEGER PRIMARY KEY,actor TEXT NOT NULL,kind TEXT NOT NULL,target TEXT NOT NULL,tick INTEGER NOT NULL)',
        f'CREATE INDEX {t("actor_events")} ON {t("events")}(actor,id)',
        f'CREATE TABLE {t("recent_interactions")}(id INTEGER PRIMARY KEY,actor TEXT NOT NULL,tick INTEGER NOT NULL,post_ordinal INTEGER NOT NULL,kind_order INTEGER NOT NULL,source_id INTEGER NOT NULL,post TEXT NOT NULL)',
        f'CREATE INDEX {t("recent_actor")} ON {t("recent_interactions")}(actor,tick DESC,post_ordinal,kind_order,source_id)',
        f'CREATE TABLE {t("embedding_pending")}(id TEXT PRIMARY KEY NOT NULL,ordinal INTEGER NOT NULL UNIQUE)',
        f'CREATE TABLE {t("vectors")}(id TEXT PRIMARY KEY NOT NULL,ordinal INTEGER NOT NULL UNIQUE,dimension INTEGER NOT NULL,vector BLOB NOT NULL)',
        f'CREATE TABLE {t("recommended")}(actor TEXT PRIMARY KEY NOT NULL,ids TEXT NOT NULL)',
        f'CREATE TABLE {t("special_tags")}(id INTEGER PRIMARY KEY,post TEXT NOT NULL,tag TEXT NOT NULL,UNIQUE(post,tag))',
        f'CREATE TABLE {t("trending")}(id INTEGER PRIMARY KEY,ids TEXT NOT NULL)',
    )


class Social:
    def __init__(self,name,store,actors,embed=None,client=None):
        self.name,self.store,self.actors=name,store,actors
        self.content_limit=store.read(lambda r:r.query(f'SELECT content_limit FROM {self.table("head")} WHERE id=1')[0][0])
        self.config=SocialNetworkConfig.model_validate_json(store.read(lambda r:r.query(f'SELECT body FROM {self.table("config")} WHERE id=1')[0][0]))
        self.embed,self.client=embed,client
        vector_id=store.read(lambda r:r.query(f'SELECT vector_id FROM {self.table("config")} WHERE id=1')[0][0])
        self._collection_name='society-posts-'+store.run_id+'-'+vector_id
        self._collection=None
        self._embedding_lock=asyncio.Lock()
        self._impressions={};self._recommended={}
        self._semantic_cache=None
        self._operations={};self._closed=False

    def _check(self):
        if self._closed:raise RuntimeError('Social is closed')

    async def close(self):
        self._closed=True
        tasks=set(self._operations)-{asyncio.current_task()}
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)

    def table(self,suffix):return _quote(self.name+'_'+suffix)

    def _member(self,r,actor):
        return bool(r.query(f'SELECT 1 FROM {self.table("members")} WHERE id=?',(actor,)))

    def _event(self,w,actor,kind,target,tick):
        w.execute(f'INSERT INTO {self.table("events")}(actor,kind,target,tick) VALUES(?,?,?,?)',(actor,kind,target,tick))

    def _notify(self,w,actor,kind,data,tick):
        w.execute(f'INSERT INTO {self.table("notices")}(actor,type,tick,consumed) VALUES(?,?,?,0)',(actor,kind,tick))
        number=w.query(f'SELECT max(id) FROM {self.table("notices")}')[0][0]
        w.execute(f'INSERT INTO {self.table("notice_data")} VALUES(?,?)',(number,json.dumps(data,ensure_ascii=False).encode()))
        w.execute(f'UPDATE {self.table("members")} SET unread=unread+1,notice_count=notice_count+1 WHERE id=?',(actor,))

    def _content_valid(self,content):return self.content_limit<0 or len(content)<=self.content_limit

    def _engagement(self,w,identifier):
        cfg=self.config.social_media.recommendation
        w.execute(f'UPDATE {self.table("posts")} SET engagement=?*like_count+?*reply_count+?*repost_count WHERE id=?',
                  (cfg.like_score,cfg.reply_score,cfg.repost_score,identifier))

    def _publish(self,w,actor,content,tags,tick,reply_to=None):
        if not self._content_valid(content):return ActionResult('rejected',{'reason':'content_length'})
        counter=w.query(f'SELECT post_count FROM {self.table("head")} WHERE id=1')[0][0]+1
        identifier='post_'+str(counter)
        w.execute(f'INSERT INTO {self.table("posts")} VALUES(?,?,?,?,?,0,0,0,0,0)',(identifier,counter,actor,tick,reply_to))
        w.execute(f'INSERT INTO {self.table("bodies")} VALUES(?,?,?)',(identifier,content.encode(),json.dumps(tags,ensure_ascii=False)))
        w.execute(f'UPDATE {self.table("head")} SET post_count=? WHERE id=1',(counter,))
        w.execute(f'UPDATE {self.table("members")} SET post_count=post_count+1 WHERE id=?',(actor,))
        if self.config.social_media.recommendation.use_embedding_similarity:
            w.execute(f'INSERT INTO {self.table("embedding_pending")} VALUES(?,?)',(identifier,counter))
        if reply_to is not None:
            w.execute(f'UPDATE {self.table("posts")} SET repost_count=repost_count+1 WHERE id=?',(reply_to,))
            self._engagement(w,reply_to)
        self._event(w,actor,'publish_post',identifier,tick)
        return ActionResult('completed',{'post_id':identifier})

    def execute(self,operation,actor,target,arguments,tick):
        self._check()
        def write(w):
            if not self._member(w,actor):return ActionResult('rejected',{'reason':'participant_unavailable'})
            if operation=='publish_post':
                if target!=actor:return ActionResult('rejected',{'reason':'participant_unavailable'})
                return self._publish(w,actor,arguments['content'],arguments.get('tags',[]),tick,arguments.get('reply_to'))
            if operation in ('follow','unfollow'):
                if target==actor or not self._member(w,target):return ActionResult('rejected',{'reason':'participant_unavailable'})
                exists=bool(w.query(f'SELECT 1 FROM {self.table("edges")} WHERE follower=? AND followee=?',(actor,target)))
                if exists==(operation=='follow'):return ActionResult('completed',{'changed':False})
                delta=1 if operation=='follow' else -1
                if delta==1:w.execute(f'INSERT INTO {self.table("edges")}(follower,followee) VALUES(?,?)',(actor,target))
                else:w.execute(f'DELETE FROM {self.table("edges")} WHERE follower=? AND followee=?',(actor,target))
                w.execute(f'UPDATE {self.table("members")} SET following=following+? WHERE id=?',(delta,actor))
                w.execute(f'UPDATE {self.table("members")} SET followers=followers+? WHERE id=?',(delta,target))
                self._event(w,actor,operation,target,tick)
                if delta==1:self._notify(w,target,'new_follower',{'follower_id':actor},tick)
                return ActionResult('completed',{'changed':True})
            rows=w.query(f'SELECT author,ordinal FROM {self.table("posts")} WHERE id=?',(target,))
            if not rows:return ActionResult('rejected',{'reason':'post_unavailable'})
            author,post_ordinal=rows[0]
            data={'post_id':target,'interactor_id':actor}
            if operation=='like_post':
                if w.query(f'SELECT 1 FROM {self.table("likes")} WHERE post=? AND actor=?',(target,actor)):
                    return ActionResult('completed',{'changed':False})
                w.execute(f'INSERT INTO {self.table("likes")}(post,actor,tick) VALUES(?,?,?)',(target,actor,tick))
                w.execute(f'INSERT INTO {self.table("recent_interactions")}(actor,tick,post_ordinal,kind_order,source_id,post) VALUES(?,?,?,0,0,?)',(actor,tick,post_ordinal,target))
                w.execute(f'UPDATE {self.table("posts")} SET like_count=like_count+1 WHERE id=?',(target,))
                self._engagement(w,target)
                notice='post_like'
                result=ActionResult('completed',{'changed':True})
            elif operation=='comment':
                content=arguments['content']
                if not self._content_valid(content):return ActionResult('rejected',{'reason':'content_length'})
                w.execute(f'INSERT INTO {self.table("replies")}(post,author,tick,body) VALUES(?,?,?,?)',(target,actor,tick,content.encode()))
                reply=w.query(f'SELECT max(id) FROM {self.table("replies")}')[0][0]
                w.execute(f'INSERT INTO {self.table("recent_interactions")}(actor,tick,post_ordinal,kind_order,source_id,post) VALUES(?,?,?,1,?,?)',(actor,tick,post_ordinal,reply,target))
                w.execute(f'UPDATE {self.table("posts")} SET reply_count=reply_count+1 WHERE id=?',(target,))
                self._engagement(w,target)
                notice='post_comment';data['reply_id']=reply
                data['comment_preview']=content.strip() if len(content.strip())<=240 else content.strip()[:240].rstrip()+'...'
                result=ActionResult('completed',{'reply_id':reply})
            elif operation=='repost':
                commentary=arguments.get('commentary') or '转发'
                if not self._content_valid(commentary):return ActionResult('rejected',{'reason':'content_length'})
                body,tags=w.query(f'SELECT body,tags FROM {self.table("bodies")} WHERE id=?',(target,))[0]
                parent=body.decode()
                cuts=[parent.find(marker) for marker in ('\n\n--- 原帖 ','\n\n--- 原贴 ') if marker in parent]
                if cuts:parent=parent[:min(cuts)].rstrip()
                content=commentary+'\n\n--- 原帖 '+target+' ---\n'+parent
                result=self._publish(w,actor,content,json.loads(tags),tick,target)
                if result.status!='completed':return result
                notice='post_repost';data['repost_id']=result.value['post_id']
                data['commentary_preview']=commentary.strip() if len(commentary.strip())<=240 else commentary.strip()[:240].rstrip()+'...'
            else:raise ValueError(operation)
            self._event(w,actor,operation,target,tick)
            if author!=actor:self._notify(w,author,notice,data,tick)
            return result
        return self.store.transaction(write)

    def post_details(self,identifier):
        def read(r):
            rows=r.query(f'SELECT id,author,created_tick,reply_to,like_count,reply_count,repost_count,view_count FROM {self.table("posts")} WHERE id=?',(identifier,))
            if not rows:raise KeyError(identifier)
            item=dict(zip(('post_id','author_id','created_tick','reply_to','like_count','reply_count','repost_count','view_count'),rows[0]))
            body,tags=r.query(f'SELECT body,tags FROM {self.table("bodies")} WHERE id=?',(identifier,))[0]
            item.update(content=body.decode(),tags=json.loads(tags))
            item['likes']=[row[0] for row in r.iter_query(f'SELECT actor FROM {self.table("likes")} WHERE post=? ORDER BY id',(identifier,))]
            item['like_events']=[{'agent_id':actor,'created_tick':tick} for actor,tick in r.iter_query(f'SELECT actor,tick FROM {self.table("likes")} WHERE post=? ORDER BY id',(identifier,))]
            item['special_tags']=[row[0] for row in r.iter_query(f'SELECT tag FROM {self.table("special_tags")} WHERE post=? ORDER BY id',(identifier,))]
            item['replies']=[{'reply_id':i,'author_id':a,'created_tick':tick,'content':body.decode()} for i,a,tick,body in r.iter_query(f'SELECT id,author,tick,body FROM {self.table("replies")} WHERE post=? ORDER BY id',(identifier,))]
            return item
        return self.store.read(read)

    def profile(self,actor):
        def read(r):
            if not self._member(r,actor):raise KeyError(actor)
            config=json.loads(r.query('SELECT body FROM actor_configs WHERE actor=?',(actor,))[0][0])
            state={key:json.loads(value) for key,value in r.query("SELECT key,value FROM actor_state WHERE actor=? AND key IN ('interests','mood')",(actor,))}
            recent=[]
            for identifier,tick,likes,replies,reposts,rowid in r.query(f'SELECT p.id,p.created_tick,p.like_count,p.reply_count,p.repost_count,b.rowid FROM {self.table("posts")} p JOIN {self.table("bodies")} b ON b.id=p.id WHERE p.author=? ORDER BY p.ordinal DESC LIMIT 5',(actor,)):
                data,total=r.read_blob(self.name+'_bodies','body',rowid,size=324)
                text=codecs.getincrementaldecoder('utf-8')().decode(data,final=len(data)==total)
                preview=text[:80]+('...' if len(text)>80 else '')
                recent.append({'post_id':identifier,'created_tick':tick,'like_count':likes,'reply_count':replies,'repost_count':reposts,'content_preview':preview,'content_path':'/'+self.name+'/content/'+identifier})
            return {'actor':actor,'type':config.get('type','unknown'),'archetype':config.get('archetype','unknown'),
                    'interests':state.get('interests',[]),'mood':state.get('mood'),'recent_posts':recent,
                    'following':[row[0] for row in r.iter_query(f'SELECT followee FROM {self.table("edges")} WHERE follower=? ORDER BY id',(actor,))],
                    'followers':[row[0] for row in r.iter_query(f'SELECT follower FROM {self.table("edges")} WHERE followee=? ORDER BY id',(actor,))],
                    'posts':[row[0] for row in r.iter_query(f'SELECT id FROM {self.table("posts")} WHERE author=? ORDER BY ordinal',(actor,))]}
        return self.store.read(read)

    def notifications(self,actor,*,consume=False,include_consumed=False):
        def read(r):
            predicate='n.actor=?'+('' if include_consumed else ' AND n.consumed=0')
            rows=r.iter_query(f'SELECT n.id,n.type,n.tick,d.data FROM {self.table("notices")} n JOIN {self.table("notice_data")} d ON d.id=n.id WHERE '+predicate+' ORDER BY n.id',(actor,))
            values=[{'id':i,'type':kind,'created_tick':tick,'data':json.loads(data)} for i,kind,tick,data in rows]
            if consume:
                r.execute(f'UPDATE {self.table("notices")} SET consumed=1 WHERE actor=? AND consumed=0',(actor,))
                r.execute(f'UPDATE {self.table("members")} SET unread=0 WHERE id=?',(actor,))
            return values
        return self.store.transaction(read) if consume else self.store.read(read)

    def active_pool(self,tick):
        cfg=self.config.social_media.recommendation
        def read(r):
            total=r.query(f'SELECT post_count FROM {self.table("head")} WHERE id=1')[0][0]
            source=self.table('posts');bindings=()
            prefix='';joined=source+' p'
            if total>cfg.full_scan_until:
                prefix=f'''WITH candidates(id) AS MATERIALIZED (
                    SELECT id FROM (SELECT id FROM {source} ORDER BY created_tick DESC,id DESC LIMIT ?)
                    UNION SELECT id FROM (SELECT id FROM {source} ORDER BY engagement DESC,created_tick DESC,id DESC LIMIT ?)
                    UNION SELECT id FROM {source} INDEXED BY {self.table("pool_recent")} WHERE created_tick>?) '''
                joined=f'candidates c CROSS JOIN {source} p ON p.id=c.id'
                bindings=(cfg.recent_keep_count,cfg.top_engagement_keep_count,tick-cfg.min_lifetime_ticks)
            rows=r.iter_query(prefix+f'SELECT p.id,p.author,p.created_tick,p.like_count,p.reply_count,p.repost_count,p.view_count,p.engagement FROM {joined} ORDER BY p.created_tick DESC,p.engagement DESC,p.id DESC',bindings)
            return [dict(zip(('post_id','author_id','created_tick','like_count','reply_count','repost_count','view_count','engagement_score'),row)) for row in rows]
        return self.store.read(read)

    def rank(self,actor,tick,*,similarity_scores=None,pool=None):
        cfg=self.config.social_media.recommendation
        pool=self.active_pool(tick) if pool is None else pool
        following=self.store.read(lambda r:{row[0] for row in r.iter_query(f'SELECT followee FROM {self.table("edges")} WHERE follower=?',(actor,))})
        similarities=similarity_scores or {};ranked=[]
        for item in pool:
            if item['author_id']==actor:continue
            time_score=math.exp(-max(tick-item['created_tick'],0)/(cfg.time_decay_hours if cfg.time_decay_hours>0 else 1.0))
            engagement=item['engagement_score']
            network=cfg.follow_bonus if item['author_id'] in following else 0.0
            semantic=similarities.get(item['post_id'],0.0)
            parts={'time_score':time_score,'time_contribution':cfg.chronological_weight*time_score,
                   'engagement_score':engagement,'engagement_contribution':cfg.engagement_weight*engagement,
                   'network_score':network,'network_contribution':cfg.network_weight*network,
                   'semantic_score':semantic,'semantic_contribution':cfg.similarity_weight*semantic}
            total=sum(parts[key] for key in ('time_contribution','engagement_contribution','network_contribution','semantic_contribution'))
            parts['total_score']=total
            ranked.append((total,item['created_tick'],dict(item,_recommendation_score={key:round(float(value),6) for key,value in parts.items()})))
        ranked.sort(key=lambda item:(item[0],item[1]),reverse=True)
        return [item[2] for item in ranked]

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
                dimension=_vectors(vectors,len(rows),dimensions[0][0] if dimensions else None)
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
    async def _ranked_feed(self,actor,tick,*,query=None):
        cfg=self.config.social_media.recommendation
        scores={}
        if cfg.use_embedding_similarity:
            await self.flush_embeddings()
            query=self.preference_text(actor) if query is None else query
            pool=[item for item in self.active_pool(tick) if item['author_id']!=actor]
            def cache_key():return (query,tuple(item['post_id'] for item in pool),(self._collection.metadata or {}).get('through',0))
            key=cache_key()
            if self._semantic_cache is not None and self._semantic_cache[0]==key:
                scores=self._semantic_cache[1]
            elif pool:
                dependencies=tuple(self.name+'_'+key for key in ('posts','bodies','edges','recent_interactions','members'))+('actor_personas',)
                source_version=self.store.read(lambda r:r.revision_for(dependencies))
                vectors=await self.embed([query],metadata={'purpose':'social_recommendation','actor':actor,'mechanism':self.name,'step':tick})
                # 网络等待期间可能新增帖子；补齐向量后再固定当前候选。
                await self.flush_embeddings()
                if self.store.read(lambda r:r.revision_for(dependencies))!=source_version:
                    raise ValueError('social preference or candidate revision changed during embedding')
                pool=[item for item in self.active_pool(tick) if item['author_id']!=actor]
                dimension=self.store.read(lambda r:r.query(f'SELECT dimension FROM {self.table("vectors")} LIMIT 1'))
                if dimension:_vectors(vectors,1,dimension[0][0])
                count=max(len(pool),cfg.post_count) if len(pool)<=cfg.full_scan_until else max(int(cfg.candidate_count*cfg.recall_multiplier),cfg.post_count)
                result=self._collection.query(query_embeddings=vectors,n_results=count,include=['distances'],where={'mechanism':{'$eq':self.name}})
                active={item['post_id'] for item in pool}
                scores={identifier:max(1-float(distance),0.0) for identifier,distance in zip(result['ids'][0],result['distances'][0]) if identifier in active}
                self._semantic_cache=(cache_key(),scores)
        else:pool=self.active_pool(tick)
        return self.rank(actor,tick,similarity_scores=scores,pool=pool)

    @_operation
    async def recommended_feed(self,actor,tick,*,record_impressions=True,query=None):
        ranked=(await self._ranked_feed(actor,tick,query=query))[:self.config.social_media.recommendation.post_count]
        output=[dict(self.post_details(item['post_id']),_recommendation_score=item['_recommendation_score']) for item in ranked]
        if record_impressions:
            for item in output:self._impressions[item['post_id']]=self._impressions.get(item['post_id'],0)+1
            self._recommended[actor]=[item['post_id'] for item in output]
        return output

    def preference_text(self,actor):
        cfg=self.config.social_media.recommendation
        record=self.actors.view(actor);sections=[]
        persona=record.persona
        if persona:sections.append('Persona:\n'+str(persona))
        if cfg.include_recent_posts_in_query and cfg.recent_post_limit:
            rows=self.store.read(lambda r:r.query(f'SELECT p.id,b.body,b.tags FROM {self.table("posts")} p JOIN {self.table("bodies")} b ON b.id=p.id WHERE p.author=? ORDER BY p.ordinal DESC LIMIT ?',(actor,cfg.recent_post_limit)))
            lines=[]
            for identifier,body,tags in rows:
                lines.append('['+identifier+'] '+body.decode()+'\nTags: '+(', '.join(json.loads(tags)) or '无标签'))
            if lines:sections.append('Recent posts:\n'+'\n---\n'.join(lines))
        if cfg.include_recent_posts_in_query and cfg.interaction_limit:
            def read(r):
                rows=r.query(f'SELECT kind_order,source_id,post FROM {self.table("recent_interactions")} WHERE actor=? ORDER BY tick DESC,post_ordinal,kind_order,source_id LIMIT ?',(actor,cfg.interaction_limit))
                result=[]
                for kind,source,post in rows:
                    table,key=(self.table('bodies'),post) if kind==0 else (self.table('replies'),source)
                    body=r.query(f'SELECT body FROM {table} WHERE id=?',(key,))[0][0].decode()
                    result.append(('Like' if kind==0 else 'Comment')+' '+post+': '+body)
                return result
            interactions=self.store.read(read)
            if interactions:sections.append('Recent interactions:\n'+'\n'.join(interactions))
        if cfg.include_following_in_query:
            follows=self.store.read(lambda r:r.query(f'SELECT followee FROM {self.table("edges")} WHERE follower=? ORDER BY id LIMIT ?',(actor,cfg.recent_post_limit or 3)))
            if follows:sections.append('Following:\n'+', '.join(row[0] for row in follows))
        return '\n\n'.join(sections) if sections else 'Social feed preference'

    @_operation
    async def after_tick(self):
        await self.flush_embeddings()
        def write(w):
            for identifier,count in self._impressions.items():
                w.execute(f'UPDATE {self.table("posts")} SET view_count=view_count+? WHERE id=?',(count,identifier))
            for actor,ids in self._recommended.items():
                w.execute(f'INSERT INTO {self.table("recommended")} VALUES(?,?) ON CONFLICT(actor) DO UPDATE SET ids=excluded.ids',(actor,json.dumps(ids)))
        if self._impressions or self._recommended:self.store.transaction(write)
        self._impressions.clear();self._recommended.clear()

    def recommended_ids(self,actor):
        rows=self.store.read(lambda r:r.query(f'SELECT ids FROM {self.table("recommended")} WHERE actor=?',(actor,)))
        return json.loads(rows[0][0]) if rows else []

    def trending(self,tick,*,limit=2,record_impressions=False):
        candidates=self.active_pool(tick)
        candidates.sort(key=lambda item:(item['engagement_score'],item['created_tick'],item['post_id']),reverse=True)
        output=[self.post_details(item['post_id']) for item in candidates[:limit]]
        if record_impressions:
            for item in output:self._impressions[item['post_id']]=self._impressions.get(item['post_id'],0)+1
        return output

    def intervene(self,tick,*,target_hashtag,draw,intervention_rate=0.5,tag_to_apply='flagged'):
        def write(w):
            rows=w.iter_query(f'SELECT p.id FROM {self.table("posts")} p JOIN {self.table("bodies")} b ON b.id=p.id WHERE instr(CAST(b.body AS TEXT),?)>0 ORDER BY p.ordinal',(target_hashtag,))
            flagged=[]
            for (identifier,) in rows:
                if draw()<intervention_rate and not w.query(f'SELECT 1 FROM {self.table("special_tags")} WHERE post=? AND tag=?',(identifier,tag_to_apply)):
                    w.execute(f'INSERT INTO {self.table("special_tags")}(post,tag) VALUES(?,?)',(identifier,tag_to_apply))
                    flagged.append(identifier)
            total=w.query(f'SELECT post_count FROM {self.table("head")} WHERE id=1')[0][0]
            return {'rule_type':'intervention','target_hashtag':target_hashtag,'intervention_rate':intervention_rate,
                    'tag_applied':tag_to_apply,'total_posts':total,'posts_flagged':len(flagged),'flagged_post_ids':flagged,'timestamp':tick}
        return self.store.transaction(write)

    def update_trending_topics(self):
        def write(w):
            ids=[row[0] for row in w.query(f'SELECT id FROM {self.table("posts")} ORDER BY engagement DESC,created_tick DESC,id DESC LIMIT 3')]
            w.execute(f'INSERT INTO {self.table("trending")} VALUES(1,?) ON CONFLICT(id) DO UPDATE SET ids=excluded.ids',(json.dumps(ids),))
            return ids
        return self.store.transaction(write)

    def trending_ids(self):
        rows=self.store.read(lambda r:r.query(f'SELECT ids FROM {self.table("trending")} WHERE id=1'))
        return json.loads(rows[0][0]) if rows else []

    def register(self,information,actions):
        def member(scope,target):return self.store.read(lambda r:self._member(r,scope.actor))
        descriptions={
            'publish_post':'以当前主体身份发布原文帖子，可指定标签和引用帖子。',
            'like_post':'点赞目标帖子；同一主体重复点赞不会重复计数。',
            'comment':'向目标帖子写入完整评论，并通知原作者。',
            'repost':'转发目标帖子，可附自己的评论；新帖子保留原帖关联。',
            'follow':'关注目标用户并通知对方。', 'unfollow':'取消对目标用户的关注。',
            'get_post_details':'读取目标帖子的完整原文、评论、点赞和系统标记。',
            'get_agent_profile':'读取目标用户的公开资料、社交关系与近期帖子预览。',
            'get_trending_posts':'读取当前热门帖子并记录本次曝光。',
            'get_notifications':'读取当前主体的全部未读通知并标记已消费；原始通知仍可追溯。',
        }
        empty={'type':'object','properties':{},'additionalProperties':False}
        text=lambda field:{'type':'object','properties':{field:{'type':'string'}},'required':[field],'additionalProperties':False}
        publish={'type':'object','properties':{'content':{'type':'string'},'tags':{'type':'array','items':{'type':'string'}},'reply_to':{'type':['string','null']}},'required':['content'],'additionalProperties':False}
        entries=(('publish_post','participants',publish,('social','social_write','publish')),
                 ('like_post','posts',empty,('social','social_write','engagement')),
                 ('comment','posts',text('content'),('social','social_write','engagement')),
                 ('repost','posts',{'type':'object','properties':{'commentary':{'type':'string'}},'additionalProperties':False},('social','social_write','engagement','publish')),
                 ('follow','participants',empty,('social','social_write','follow')),
                 ('unfollow','participants',empty,('social','social_write','follow')))
        for operation,kind,schema,tags in entries:
            def available(scope,target,operation=operation,kind=kind):
                if not member(scope,target):return False
                if operation=='publish_post':return target.key==scope.actor
                if kind=='participants':return target.key!=scope.actor and self.store.read(lambda r:self._member(r,target.key))
                return self.store.read(lambda r:bool(r.query(f'SELECT 1 FROM {self.table("posts")} WHERE id=?',(target.key,))))
            actions.register(Action(self.name+'.'+operation,(self.name,kind),descriptions[operation],schema,
                lambda scope,target,args,operation=operation:self.execute(operation,scope.actor,target.key,args,scope.moment.time),tags=tags,available=available),
                dependencies=(self.name+'_members',self.name+'_posts',self.name+'_edges'))
        def details(scope,target,args):return ActionResult('completed',self.post_details(target.key))
        def profile(scope,target,args):return ActionResult('completed',self.profile(target.key))
        def notices(scope,target,args):return ActionResult('completed',{'notifications':self.notifications(scope.actor,consume=True)})
        def trending(scope,target,args):return ActionResult('completed',{'posts':self.trending(scope.moment.time,record_impressions=True)})
        for operation,kind,handler,read_only in (('get_post_details','posts',details,True),('get_agent_profile','participants',profile,True),('get_trending_posts','participants',trending,False),('get_notifications','participants',notices,False)):
            def available(scope,target,kind=kind,operation=operation):
                if not member(scope,target):return False
                if operation in ('get_trending_posts','get_notifications'):return target.key==scope.actor
                table=self.table('posts' if kind=='posts' else 'members')
                return self.store.read(lambda r:bool(r.query(f'SELECT 1 FROM {table} WHERE id=?',(target.key,))))
            actions.register(Action(self.name+'.'+operation,(self.name,kind),descriptions[operation],empty,handler,available=available,
                tags=('social_read','lookup'),read_only=read_only),dependencies=(self.name+'_members',self.name+'_posts'))
        public=lambda scope:('EXISTS(SELECT 1 FROM '+self.table('members')+' WHERE id=?)',(scope.actor,))
        info=SocialInformation(self,{
            'feed':DatasetSpec(self.name+'_posts','id',('id',),authorize=public),
            'participants':DatasetSpec(self.name+'_members','id',('id','post_count','followers','following'),authorize=public),
            'posts':DatasetSpec(self.name+'_posts','id',('id','author','created_tick','reply_to','like_count','reply_count','repost_count','view_count'),authorize=public,order_fields=('ordinal','created_tick'),
                base_count=lambda scope:(f'SELECT post_count FROM {self.table("head")} WHERE id=1 AND EXISTS(SELECT 1 FROM {self.table("members")} WHERE id=?)',(scope.actor,)),dependencies=(self.name+'_head',self.name+'_members')),
            'content':DocumentSpec(self.name+'_bodies','id','body',authorize=public,dependencies=(self.name+'_members',)),
            'replies':DatasetSpec(self.name+'_replies','id',('id','post','author','tick'),authorize=public,order_fields=('tick',),dependencies=(self.name+'_members',)),
            'reply_content':DocumentSpec(self.name+'_replies','id','body',authorize=public,dependencies=(self.name+'_members',)),
            'notification_data':DocumentSpec(self.name+'_notice_data','id','data',authorize=lambda scope:(f'EXISTS(SELECT 1 FROM {self.table("notices")} n WHERE n.id={self.table("notice_data")}.id AND n.actor=?)',(scope.actor,)),dependencies=(self.name+'_notices',)),
            'notifications':DatasetSpec(self.name+'_notices','id',('id','type','tick','consumed'),authorize=lambda scope:('actor=? AND consumed=0',(scope.actor,)),
                base_count=lambda scope:(f'SELECT unread FROM {self.table("members")} WHERE id=?',(scope.actor,)),dependencies=(self.name+'_members',)),
        })
        information.mount('/'+self.name,info)


class SocialInformation(SQLInformation):
    """推荐次序由机制定义；页面传元数据，正文保留独立范围入口。"""
    def __init__(self,social,routes):
        self.social=social
        super().__init__(social.name,social.store,routes)

    async def query(self,scope,path,query):
        route,key=self._route(path)
        if route!='feed':return await super().query(scope,path,query)
        if key is not None:raise Unavailable('resource unavailable')
        if query.fields or query.filters or query.order or query.sample_seed is not None:
            raise ValueError('ranked feed supports limit and cursor; use posts for structured queries')
        if type(query.limit) is not int or query.limit<1:raise ValueError('limit must be positive')
        social=self.social
        deps=tuple(social.name+'_'+suffix for suffix in ('posts','bodies','edges','recent_interactions','vectors','members'))+('actor_personas',)+self.access_dependencies
        def version(r):
            if not social._member(r,scope.actor):raise Unavailable('resource unavailable')
            return [r.run_id,r.revision_for(deps)]
        def identity(version):return json.dumps([scope.actor,asdict(scope.moment),path,version],sort_keys=True)
        before=social.store.read(version,expected_revision=scope.revision)
        offset=0
        if query.cursor is not None:
            if query.cursor['identity']!=identity(before):raise ValueError('feed cursor mismatch')
            offset=query.cursor['offset']
            if type(offset) is not int or offset<0:raise ValueError('invalid cursor offset')
        ranked=await social._ranked_feed(scope.actor,scope.moment.time)
        scope.check_active()
        after=social.store.read(version,expected_revision=scope.revision)
        if query.cursor is not None and before!=after:raise ValueError('feed cursor revision changed')
        selected=[]
        for item in ranked[offset:offset+query.limit]:
            selected.append(dict(item,ref=Ref(social.name,'posts',item['post_id']),
                                 content_path='/'+social.name+'/content/'+item['post_id']))
        end=offset+len(selected)
        cursor={'identity':identity(after),'offset':end} if end<len(ranked) else None
        return Page(selected,len(ranked),cursor,after[1])


def social_plugin(members,*,name='social',edges=None,config=None,seed=0,content_length_limit=None,embedding=None,vector_client=None,storage='storage',actors='actors',interaction='interaction'):
    members=tuple(members)
    config=SocialNetworkConfig.model_validate(config or {})
    if content_length_limit is None:content_length_limit=config.social_media.content_length_limit
    else:config.social_media.content_length_limit=content_length_limit
    edges=None if edges is None else tuple(dict.fromkeys(tuple(pair) for pair in edges))
    if len(set(members))!=len(members):raise ValueError('social members must be distinct')
    if edges is not None and any(a not in members or b not in members or a==b for a,b in edges):raise ValueError('invalid social edge')
    if type(content_length_limit) is not int or content_length_limit < -1:raise ValueError('invalid content length')
    if not name or '/' in name:raise ValueError('mechanism name must be one path segment')
    t=lambda suffix:_quote(name+'_'+suffix)
    def initialize(w):
        w.execute(f'INSERT INTO {t("config")} VALUES(1,?,?)',(config.model_dump_json(),uuid.uuid4().hex))
        w.execute(f'INSERT INTO {t("head")} VALUES(1,0,?,?)',(content_length_limit,len(members)))
        w.executemany(f'INSERT INTO {t("members")} VALUES(?,?,0,0,0,0,0)',((actor,index) for index,actor in enumerate(members)))
        for a,b in (generate_topology(members,config,seed=seed).edges if edges is None else edges):
            w.execute(f'INSERT INTO {t("edges")}(follower,followee) VALUES(?,?)',(a,b))
            w.execute(f'UPDATE {t("members")} SET following=following+1 WHERE id=?',(a,))
            w.execute(f'UPDATE {t("members")} SET followers=followers+1 WHERE id=?',(b,))
    def install(ctx):
        embed=ctx.require(embedding[0],'embeddings')[embedding[1]].embed if embedding else None
        client=ctx.require(*vector_client) if vector_client else None
        social=Social(name,ctx.require(storage,'store'),ctx.require(actors,'actors'),embed,client)
        ctx.on_close(social.close)
        ctx.on_step(after=social.after_tick)
        social.register(ctx.require(interaction,'information'),ctx.require(interaction,'actions'))
        ctx.provide('mechanism',social)
    requires=tuple(dict.fromkeys((storage,actors,interaction)+((embedding[0],) if embedding else ())+((vector_client[0],) if vector_client else ())))
    return Plugin(name,requires=requires,install=install,schema=_schema(name),initialize=initialize)
