"""共享社交机制：不可变正文、当前计数与关系索引。"""
import json
import uuid

from ..kernel.plugins import Plugin
from ..kernel.interaction import Action, ActionResult, Page, Ref, Unavailable
from dataclasses import asdict
from ..kernel.information_sql import SQLInformation, DatasetSpec, DocumentSpec, _quote
from .social_models import SocialNetworkConfig
from .social_domain import _post_details, _profile


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
    """对外组合入口；领域状态、任务和投影由被借用的叶服务持有。"""
    def __init__(self,data,content,relations,notifications,exposure,candidates,recommendation,semantic=None):
        self.data=data
        self.name,self.store,self.config=data.name,data.store,data.config
        self.content,self.relations,self.notices=content,relations,notifications
        self.exposure,self.candidates,self.recommendations,self.semantic=exposure,candidates,recommendation,semantic

    def table(self,suffix):return self.data.table(suffix)
    def _member(self,r,actor):return self.data.member_in(r,actor)

    def execute(self,operation,actor,target,arguments,tick):
        def write(w):
            if not self.data.member_in(w,actor):return ActionResult('rejected',{'reason':'participant_unavailable'})
            if operation in ('follow','unfollow'):return self.relations.execute_in(w,operation,actor,target,tick)
            return self.content.execute_in(w,operation,actor,target,arguments,tick)
        return self.store.transaction(write)

    def post_details(self,identifier):return self.content.post_details(identifier)
    def profile(self,actor):return self.store.read(lambda r:_profile(r,self.name,actor))
    def notifications(self,actor,**kwargs):return self.notices.notifications(actor,**kwargs)
    def active_pool(self,tick):return self.candidates.active_pool(tick)
    def intervene(self,tick,**kwargs):return self.content.intervene(tick,**kwargs)
    def update_trending_topics(self):return self.content.update_trending_topics()
    def trending_ids(self):return self.content.trending_ids()
    def recommended_ids(self,actor):return self.exposure.recommended_ids(actor)
    def set_recommended(self,actor,ids):self.exposure.set_recommended(actor,ids)

    def ranking_input(self,actor,tick,**kwargs):return self.recommendations.ranking_input(actor,tick,**kwargs)
    def ranking_dependencies(self):return self.recommendations.ranking_dependencies()

    async def recommendation_input(self,actor,tick,*,query=None):
        return await self.recommendations.recommendation_input(actor,tick,query=query)

    def rank(self,actor,tick,*,similarity_scores=None,pool=None):
        source=self.ranking_input(actor,tick,similarity_scores=similarity_scores,pool=pool)
        return self._rank_metadata(source,self.recommendations.rank(source).items)

    @staticmethod
    def _rank_metadata(source,items):
        return [dict(asdict(source.by_id[item.post_id]),_recommendation_score={'total_score':round(float(item.score),6),**dict(item.components)}) for item in items]

    async def recommendation(self,actor,tick,*,query=None):
        """一次构建输入并调用绑定策略，返回可独立消费的完整值对象。"""
        return await self.recommendations.recommendation(actor,tick,query=query)

    async def recommended_feed(self,actor,tick,*,record_impressions=True,query=None):
        source,result=await self.recommendation(actor,tick,query=query)
        ranked=self._rank_metadata(source,result.items[:self.config.social_media.recommendation.post_count])
        output=[dict(self.post_details(item['post_id']),_recommendation_score=item['_recommendation_score']) for item in ranked]
        if record_impressions:self.present(actor,[item['post_id'] for item in output])
        return output

    def present(self,actor,ids,*,recommended=True):
        """消费者确认实际呈现的 id 后调用；预览、通用读取均无曝光。"""
        self.exposure.record(actor,ids,recommended=recommended)

    def preference_text(self,actor):
        return self.data.preference_text(actor)

    async def flush_embeddings(self):
        if self.semantic is not None:await self.semantic.flush_embeddings()

    async def after_tick(self):
        await self.flush_embeddings()
        self.exposure.flush()

    def trending(self,tick,*,limit=2,record_impressions=False,actor=None):
        ids=self.candidates.trending_ids(limit)
        output=[self.post_details(identifier) for identifier in ids]
        if record_impressions:
            if actor is None:raise ValueError('actual trending presentation requires actor')
            self.present(actor,ids,recommended=False)
        return output

    def register(self,information,actions):
        def member(scope,target):return self.store.read(lambda r:self._member(r,scope.actor))
        descriptions={
            'publish_post':'以当前主体身份发布原文帖子，可指定标签和引用帖子。',
            'like_post':'点赞目标帖子；同一主体重复点赞不会重复计数。',
            'comment':'向目标帖子写入完整评论，并通知原作者。',
            'repost':'转发目标帖子，可附自己的评论；新帖子保留原帖关联。',
            'follow':'关注目标用户并通知对方。', 'unfollow':'取消对目标用户的关注。',
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
        def notices(scope,target,args):return ActionResult('completed',{'notifications':self.notifications(scope.actor,consume=True)})
        def trending(scope,target,args):return ActionResult('completed',{'posts':self.trending(scope.moment.time,record_impressions=True,actor=scope.actor)})
        for operation,kind,handler,read_only in (('get_trending_posts','participants',trending,False),('get_notifications','participants',notices,False)):
            def available(scope,target,kind=kind,operation=operation):
                if not member(scope,target):return False
                if operation in ('get_trending_posts','get_notifications'):return target.key==scope.actor
                table=self.table('posts' if kind=='posts' else 'members')
                return self.store.read(lambda r:bool(r.query(f'SELECT 1 FROM {table} WHERE id=?',(target.key,))))
            actions.register(Action(self.name+'.'+operation,(self.name,kind),descriptions[operation],empty,handler,available=available,
                tags=('social_read','lookup'),read_only=read_only),dependencies=(self.name+'_members',self.name+'_posts'))
        provider=SocialInformation(self,_social_routes(self.name))
        information.mount('/'+self.name,provider)
        return provider




def _social_routes(name):
    table=lambda suffix:_quote(name+'_'+suffix)
    public=lambda scope:('EXISTS(SELECT 1 FROM '+table('members')+' WHERE id=?)',(scope.actor,))
    return {
        'post_details':DatasetSpec(name+'_posts','id',('id',),authorize=public,dependencies=tuple(name+'_'+suffix for suffix in ('members','bodies','likes','special_tags','replies'))),
        'profiles':DatasetSpec(name+'_members','id',('id',),authorize=public,dependencies=(name+'_posts',name+'_bodies',name+'_edges','actor_configs','actor_state')),
        'feed':DatasetSpec(name+'_posts','id',('id',),authorize=public),
        'participants':DatasetSpec(name+'_members','id',('id','post_count','followers','following'),authorize=public),
        'posts':DatasetSpec(name+'_posts','id',('id','author','created_tick','reply_to','like_count','reply_count','repost_count','view_count'),authorize=public,order_fields=('ordinal','created_tick'),
            field_descriptions={'id':'帖子标识','author':'作者主体标识','created_tick':'创建时的仿真 tick','reply_to':'被回复的帖子标识，原创为空','like_count':'累计点赞数','reply_count':'累计回复数','repost_count':'累计转发数','view_count':'累计浏览数'},
            time_description='created_tick 使用发布动作传入的仿真 tick，与该环境的离散运行时间口径一致。',
            base_count=lambda scope:(f'SELECT post_count FROM {table("head")} WHERE id=1 AND EXISTS(SELECT 1 FROM {table("members")} WHERE id=?)',(scope.actor,)),dependencies=(name+'_head',name+'_members')),
        'content':DocumentSpec(name+'_bodies','id','body',authorize=public,dependencies=(name+'_members',)),
        'replies':DatasetSpec(name+'_replies','id',('id','post','author','tick'),authorize=public,order_fields=('tick',),dependencies=(name+'_members',)),
        'reply_content':DocumentSpec(name+'_replies','id','body',authorize=public,dependencies=(name+'_members',)),
        'notification_data':DocumentSpec(name+'_notice_data','id','data',authorize=lambda scope:(f'EXISTS(SELECT 1 FROM {table("notices")} n WHERE n.id={table("notice_data")}.id AND n.actor=?)',(scope.actor,)),dependencies=(name+'_notices',)),
        'notifications':DatasetSpec(name+'_notices','id',('id','type','tick','consumed'),authorize=lambda scope:('actor=? AND consumed=0',(scope.actor,)),
            base_count=lambda scope:(f'SELECT unread FROM {table("members")} WHERE id=?',(scope.actor,)),dependencies=(name+'_members',)),
    }


class _SocialSQLInformation(SQLInformation):
    """帖子资料沿同一页面物化路径提供正文入口。"""
    async def read(self,scope,path,*,offset=0,size=65536):
        route,key=self._route(path)
        if route not in ('post_details','profiles') or key is None:
            return await super().read(scope,path,offset=offset,size=size)
        scope.check_active()
        if offset<0 or size<1:raise ValueError('invalid social document range')
        spec=self._routes[route];where,values=self._where(scope,spec,())
        def read(view):
            rows=view.query('SELECT '+_quote(spec.key)+' FROM '+_quote(spec.table)+' WHERE '+where+' AND '+_quote(spec.key)+'=?',(*values,key),max_rows=1)
            if not rows:raise Unavailable('resource unavailable')
            revision=view.revision_for((spec.table,*spec.dependencies,*self._access_dependencies(scope)))
            identity=(scope.actor,path,revision)
            if self._record_source is None or self._record_source[0]!=identity:
                record=(_post_details if route=='post_details' else _profile)(view,self.namespace,key)
                self._materialize_record(identity,record)
            return self._record_range(offset,size,revision,self.ref(path))
        return self.reader.read(read,expected_revision=scope.revision)

    def _items(self, route, fields, selected, rows, view=None, revision=None):
        items=super()._items(route,fields,selected,rows,view,revision)
        if route=='posts':
            for item in items:
                item['content_path']='/'+self.namespace+'/content/'+item['ref'].key
                item['logical_path']='/world'+item['content_path']
        return items


def social_information(reader,name='social'):
    """只读 SQL 资料；推荐 feed 仍由活动机制的显式调用提供。"""
    routes=_social_routes(name)
    routes.pop('feed')
    return _SocialSQLInformation(name,reader,routes)


class SocialInformation(_SocialSQLInformation):
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
        deps=social.ranking_dependencies()+self._access_dependencies(scope)
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
        source,ranked=await social.recommendation(scope.actor,scope.moment.time)
        scope.check_active()
        after=social.store.read(version,expected_revision=scope.revision)
        if query.cursor is not None and before!=after:raise ValueError('feed cursor revision changed')
        selected=[]
        for item in social._rank_metadata(source,ranked.items[offset:offset+query.limit]):
            selected.append(dict(item,ref=Ref(social.name,'posts',item['post_id']),
                                 content_path='/'+social.name+'/content/'+item['post_id']))
        end=offset+len(selected)
        cursor={'identity':identity(after),'offset':end} if end<ranked.total else None
        return Page(selected,ranked.total,cursor,after[1])




def social_data_plugin(members,*,name='social.data',domain='social',edges=None,config=None,seed=0,content_length_limit=None,storage='storage',actors='actors.data'):
    from .social_domain import SocialData
    members=tuple(members)
    config=SocialNetworkConfig.model_validate(config or {}).model_copy(deep=True)
    if content_length_limit is None:content_length_limit=config.social_media.content_length_limit
    else:config.social_media.content_length_limit=content_length_limit
    edges=None if edges is None else tuple(dict.fromkeys(tuple(pair) for pair in edges))
    if len(set(members))!=len(members):raise ValueError('social members must be distinct')
    if edges is not None and any(a not in members or b not in members or a==b for a,b in edges):raise ValueError('invalid social edge')
    if type(content_length_limit) is not int or content_length_limit < -1:raise ValueError('invalid content length')
    if not domain or '/' in domain:raise ValueError('mechanism name must be one path segment')
    t=lambda suffix:_quote(domain+'_'+suffix)
    def initialize(w):
        w.execute(f'INSERT INTO {t("config")} VALUES(1,?,?)',(config.model_dump_json(),uuid.uuid4().hex))
        w.execute(f'INSERT INTO {t("head")} VALUES(1,0,?,?)',(content_length_limit,len(members)))
        w.executemany(f'INSERT INTO {t("members")} VALUES(?,?,0,0,0,0,0)',((actor,index) for index,actor in enumerate(members)))
        if edges is None:
            if config.distribution.type in ('random','complete'):
                from itertools import combinations,permutations
                from random import Random
                rng=Random(seed);adjacency={actor:[] for actor in members}
                probability=config.distribution.params.connection_probability if config.distribution.type=='random' else 1
                pairs=permutations(members,2) if config.is_directed and config.distribution.type=='random' else combinations(members,2)
                for a,b in pairs:
                    if probability>=1 or (probability>0 and rng.random()<probability):
                        adjacency[a].append(b)
                        if not config.is_directed:adjacency[b].append(a)
                initial_edges=((a,b) for a in members for b in adjacency[a])
            else:
                from .social_topology import generate_topology
                initial_edges=generate_topology(members,config,seed=seed).edges
        else:initial_edges=edges
        for a,b in initial_edges:
            w.execute(f'INSERT INTO {t("edges")}(follower,followee) VALUES(?,?)',(a,b))
            w.execute(f'UPDATE {t("members")} SET following=following+1 WHERE id=?',(a,))
            w.execute(f'UPDATE {t("members")} SET followers=followers+1 WHERE id=?',(b,))
    def install(ctx):ctx.provide('data',SocialData(domain,ctx.require(storage,'store'),ctx.require(actors,'directory')))
    schema=tuple(sql for sql in _schema(domain) if not any(t(suffix) in sql for suffix in ('vectors','embedding_pending')))
    return Plugin(name,requires=(storage,actors),schema_requires=(actors,),schema=schema,initialize=initialize,install=install)


def social_notifications_plugin(*,name='social.notifications',data='social.data'):
    from .social_domain import Notifications
    return Plugin(name,requires=(data,),install=lambda ctx:ctx.provide('notifications',Notifications(ctx.require(data,'data'))))


def social_relations_plugin(*,name='social.relations',data='social.data',notifications='social.notifications'):
    from .social_domain import Relations
    return Plugin(name,requires=(data,notifications),install=lambda ctx:ctx.provide('relations',Relations(ctx.require(data,'data'),ctx.require(notifications,'notifications'))))


def social_engagement_plugin(*,name='social.engagement',data='social.data'):
    from .social_domain import Engagement
    return Plugin(name,requires=(data,),install=lambda ctx:ctx.provide('engagement',Engagement(ctx.require(data,'data'))))


def social_content_plugin(*,name='social.content',data='social.data',notifications='social.notifications',engagement='social.engagement',semantic=None):
    from .social_domain import Content
    def install(ctx):ctx.provide('content',Content(ctx.require(data,'data'),ctx.require(notifications,'notifications'),ctx.require(engagement,'engagement'),ctx.require(semantic,'semantic') if semantic else None))
    return Plugin(name,requires=(data,notifications,engagement)+((semantic,) if semantic else ()),install=install)


def social_exposure_plugin(*,name='social.exposure',data='social.data'):
    from .social_domain import Exposure
    def install(ctx):
        exposure=Exposure(ctx.require(data,'data'))
        ctx.provide('exposure',exposure)
        ctx.on_step(after=exposure.flush)
    return Plugin(name,requires=(data,),install=install)


def social_candidates_plugin(*,name='social.candidates',data='social.data'):
    from .social_domain import ActivePool
    return Plugin(name,requires=(data,),install=lambda ctx:ctx.provide('candidates',ActivePool(ctx.require(data,'data'))))


def social_semantic_plugin(*,name='social.semantic',domain='social',data='social.data',candidates='social.candidates',embedding=None,vector_client=None,cache_capacity=128):
    if embedding is None or vector_client is None:raise ValueError('semantic recommendations require embedding and vector resources')
    def install(ctx):
        from .social_semantic import Semantic
        embed=ctx.require(embedding[0],'embeddings')[embedding[1]].embed if embedding else None
        client=ctx.require(*vector_client) if vector_client else None
        semantic=Semantic(ctx.require(data,'data'),ctx.require(candidates,'candidates'),embed,client,cache_capacity=cache_capacity)
        ctx.provide('semantic',semantic)
        ctx.on_quiesce(semantic.close)
        ctx.on_step(after=semantic.flush_embeddings)
    t=lambda suffix:_quote(domain+'_'+suffix)
    schema=tuple(sql for sql in _schema(domain) if any(t(suffix) in sql for suffix in ('vectors','embedding_pending')))
    requires=tuple(dict.fromkeys((data,candidates)+((embedding[0],) if embedding else ())+((vector_client[0],) if vector_client else ())))
    return Plugin(name,requires=requires,schema_requires=(data,),schema=schema,install=install)


def social_presentation_plugin(*,name='social.presentation',data='social.data',content='social.content',relations='social.relations',notifications='social.notifications',exposure='social.exposure',candidates='social.candidates',recommendation='social.recommendation',semantic=None,interaction='interaction'):
    def install(ctx):
        presentation=Social(ctx.require(data,'data'),ctx.require(content,'content'),ctx.require(relations,'relations'),ctx.require(notifications,'notifications'),ctx.require(exposure,'exposure'),ctx.require(candidates,'candidates'),ctx.require(recommendation,'recommendation'),ctx.require(semantic,'semantic') if semantic else None)
        provider=presentation.register(ctx.require(interaction,'information'),ctx.require(interaction,'actions'))
        ctx.on_close(provider.close)
        ctx.provide('presentation',presentation)
        ctx.provide('information',provider)
    requires=tuple(dict.fromkeys((data,content,relations,notifications,exposure,candidates,recommendation,interaction)+((semantic,) if semantic else ())))
    return Plugin(name,requires=requires,install=install)


from .social_recommendation import social_recommendation_plugin
from .social_ranking import (Candidate, RankingInput, RankedPost, RankingOutput,
                             weighted_ranking_plugin, chronological_ranking_plugin)


def social_plugin(members,*,name='social',edges=None,config=None,seed=0,content_length_limit=None,embedding=None,vector_client=None,storage='storage',actors='actors.data',interaction='interaction',ranking=None,cache_capacity=128):
    cfg=SocialNetworkConfig.model_validate(config or {})
    if cfg.social_media.recommendation.use_embedding_similarity:
        if embedding is None or vector_client is None:raise ValueError('semantic recommendations require embedding and vector resources')
    elif embedding is not None or vector_client is not None:
        raise ValueError('semantic resources require use_embedding_similarity=True')
    data=name+'.data';notifications=name+'.notifications';relations=name+'.relations'
    engagement=name+'.engagement';content=name+'.content';exposure=name+'.exposure'
    candidates=name+'.candidates';presentation=name+'.presentation';recommendation=name+'.recommendation'
    semantic=name+'.semantic' if cfg.social_media.recommendation.use_embedding_similarity else None
    includes=[social_data_plugin(members,name=data,domain=name,edges=edges,config=cfg,seed=seed,content_length_limit=content_length_limit,storage=storage,actors=actors),
              social_notifications_plugin(name=notifications,data=data),
              social_relations_plugin(name=relations,data=data,notifications=notifications),
              social_engagement_plugin(name=engagement,data=data),
              social_candidates_plugin(name=candidates,data=data),
              social_exposure_plugin(name=exposure,data=data)]
    if semantic:includes.append(social_semantic_plugin(name=semantic,domain=name,data=data,candidates=candidates,embedding=embedding,vector_client=vector_client,cache_capacity=cache_capacity))
    if ranking is None:
        ranking=(name+'.weighted','rank')
        includes.append(weighted_ranking_plugin(name=ranking[0],data=data))
    includes.append(social_content_plugin(name=content,data=data,notifications=notifications,engagement=engagement,semantic=semantic))
    includes.append(social_recommendation_plugin(name=recommendation,data=data,candidates=candidates,relations=relations,ranking=ranking,semantic=semantic,cache_capacity=cache_capacity))
    includes.append(social_presentation_plugin(name=presentation,data=data,content=content,relations=relations,notifications=notifications,exposure=exposure,candidates=candidates,recommendation=recommendation,semantic=semantic,interaction=interaction))
    return Plugin(name,includes=tuple(includes),requires=(presentation,),install=lambda ctx:ctx.provide('mechanism',ctx.require(presentation,'presentation')))
