"""领域服务分别拥有写入职责，通过事务内写入器原子协作。"""
import json
import codecs
from ..kernel.interaction import ActionResult
from ..kernel.information_sql import _quote
from .social_models import SocialNetworkConfig


def _post_details(r,name,identifier):
    table=lambda suffix:_quote(name+'_'+suffix)
    rows=r.query(f'SELECT id,author,created_tick,reply_to,like_count,reply_count,repost_count,view_count FROM {table("posts")} WHERE id=?',(identifier,))
    if not rows:raise KeyError(identifier)
    item=dict(zip(('post_id','author_id','created_tick','reply_to','like_count','reply_count','repost_count','view_count'),rows[0]))
    body,tags=r.query(f'SELECT body,tags FROM {table("bodies")} WHERE id=?',(identifier,))[0]
    item.update(content=body.decode(),tags=json.loads(tags))
    item['likes']=[row[0] for row in r.iter_query(f'SELECT actor FROM {table("likes")} WHERE post=? ORDER BY id',(identifier,))]
    item['like_events']=[{'agent_id':actor,'created_tick':tick} for actor,tick in r.iter_query(f'SELECT actor,tick FROM {table("likes")} WHERE post=? ORDER BY id',(identifier,))]
    item['special_tags']=[row[0] for row in r.iter_query(f'SELECT tag FROM {table("special_tags")} WHERE post=? ORDER BY id',(identifier,))]
    item['replies']=[{'reply_id':i,'author_id':a,'created_tick':tick,'content':body.decode()} for i,a,tick,body in r.iter_query(f'SELECT id,author,tick,body FROM {table("replies")} WHERE post=? ORDER BY id',(identifier,))]
    return item

def _profile(r,name,actor):
    table=lambda suffix:_quote(name+'_'+suffix)
    if not bool(r.query('SELECT 1 FROM '+table("members")+' WHERE id=?',(actor,))):raise KeyError(actor)
    config=json.loads(r.query('SELECT body FROM actor_configs WHERE actor=?',(actor,))[0][0])
    state={key:json.loads(value) for key,value in r.query("SELECT key,value FROM actor_state WHERE actor=? AND key IN ('interests','mood')",(actor,))}
    recent=[]
    for identifier,tick,likes,replies,reposts,rowid in r.query(f'SELECT p.id,p.created_tick,p.like_count,p.reply_count,p.repost_count,b.rowid FROM {table("posts")} p JOIN {table("bodies")} b ON b.id=p.id WHERE p.author=? ORDER BY p.ordinal DESC LIMIT 5',(actor,)):
        data,total=r.read_blob(name+'_bodies','body',rowid,size=324)
        text=codecs.getincrementaldecoder('utf-8')().decode(data,final=len(data)==total)
        preview=text[:80]+('...' if len(text)>80 else '')
        recent.append({'post_id':identifier,'created_tick':tick,'like_count':likes,'reply_count':replies,'repost_count':reposts,'content_preview':preview,'content_path':'/'+name+'/content/'+identifier,'logical_path':'/world/'+name+'/content/'+identifier})
    return {'actor':actor,'type':config.get('type','unknown'),'archetype':config.get('archetype','unknown'),
            'interests':state.get('interests',[]),'mood':state.get('mood'),'recent_posts':recent,
            'following':[row[0] for row in r.iter_query(f'SELECT followee FROM {table("edges")} WHERE follower=? ORDER BY id',(actor,))],
            'followers':[row[0] for row in r.iter_query(f'SELECT follower FROM {table("edges")} WHERE followee=? ORDER BY id',(actor,))],
            'posts':[row[0] for row in r.iter_query(f'SELECT id FROM {table("posts")} WHERE author=? ORDER BY ordinal',(actor,))]}


class SocialData:
    def __init__(self,name,store,actors):
        self.name,self.store,self.actors=name,store,actors
        self.content_limit=store.read(lambda r:r.query(f'SELECT content_limit FROM {self.table("head")} WHERE id=1')[0][0])
        self.config=SocialNetworkConfig.model_validate_json(store.read(lambda r:r.query(f'SELECT body FROM {self.table("config")} WHERE id=1')[0][0]))

    def table(self,suffix):return _quote(self.name+'_'+suffix)

    def member_in(self,r,actor):
        return bool(r.query(f'SELECT 1 FROM {self.table("members")} WHERE id=?',(actor,)))

    def event_in(self,w,actor,kind,target,tick):
        w.execute(f'INSERT INTO {self.table("events")}(actor,kind,target,tick) VALUES(?,?,?,?)',(actor,kind,target,tick))


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



class DomainService:
    def __init__(self,data):
        self.data=data
        self.name,self.store,self.config=data.name,data.store,data.config
    def table(self,suffix):return self.data.table(suffix)


class Notifications(DomainService):
    def notify_in(self,w,actor,kind,data,tick):
        w.execute(f'INSERT INTO {self.table("notices")}(actor,type,tick,consumed) VALUES(?,?,?,0)',(actor,kind,tick))
        number=w.query(f'SELECT max(id) FROM {self.table("notices")}')[0][0]
        w.execute(f'INSERT INTO {self.table("notice_data")} VALUES(?,?)',(number,json.dumps(data,ensure_ascii=False).encode()))
        w.execute(f'UPDATE {self.table("members")} SET unread=unread+1,notice_count=notice_count+1 WHERE id=?',(actor,))

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

class Relations(DomainService):
    def __init__(self,data,notifications):
        super().__init__(data);self.notices=notifications

    def following(self,actor):
        return self.store.read(lambda r:frozenset(row[0] for row in r.iter_query(f'SELECT followee FROM {self.table("edges")} WHERE follower=?',(actor,))))

    def execute_in(self,w,operation,actor,target,tick):
        if target==actor or not self.data.member_in(w,target):return ActionResult('rejected',{'reason':'participant_unavailable'})
        exists=bool(w.query(f'SELECT 1 FROM {self.table("edges")} WHERE follower=? AND followee=?',(actor,target)))
        if exists==(operation=='follow'):return ActionResult('completed',{'changed':False})
        delta=1 if operation=='follow' else -1
        if delta==1:w.execute(f'INSERT INTO {self.table("edges")}(follower,followee) VALUES(?,?)',(actor,target))
        else:w.execute(f'DELETE FROM {self.table("edges")} WHERE follower=? AND followee=?',(actor,target))
        w.execute(f'UPDATE {self.table("members")} SET following=following+? WHERE id=?',(delta,actor))
        w.execute(f'UPDATE {self.table("members")} SET followers=followers+? WHERE id=?',(delta,target))
        self.data.event_in(w,actor,operation,target,tick)
        if delta==1:self.notices.notify_in(w,target,'new_follower',{'follower_id':actor},tick)
        return ActionResult('completed',{'changed':True})


class Engagement(DomainService):
    """事实计数的配置化派生投影；替换排名策略不修改互动事实。"""
    def update_in(self,w,identifier):
        cfg=self.config.social_media.recommendation
        w.execute(f'UPDATE {self.table("posts")} SET engagement=?*like_count+?*reply_count+?*repost_count WHERE id=?',
                  (cfg.like_score,cfg.reply_score,cfg.repost_score,identifier))

class Content(DomainService):
    def __init__(self,data,notifications,engagement,semantic=None):
        super().__init__(data)
        self.notices,self.engagement,self.semantic=notifications,engagement,semantic
        self.content_limit=data.content_limit

    def _content_valid(self,content):return self.content_limit<0 or len(content)<=self.content_limit

    def publish_in(self,w,actor,content,tags,tick,reply_to=None):
        if not self._content_valid(content):return ActionResult('rejected',{'reason':'content_length'})
        counter=w.query(f'SELECT post_count FROM {self.table("head")} WHERE id=1')[0][0]+1
        identifier='post_'+str(counter)
        w.execute(f'INSERT INTO {self.table("posts")} VALUES(?,?,?,?,?,0,0,0,0,0)',(identifier,counter,actor,tick,reply_to))
        w.execute(f'INSERT INTO {self.table("bodies")} VALUES(?,?,?)',(identifier,content.encode(),json.dumps(tags,ensure_ascii=False)))
        w.execute(f'UPDATE {self.table("head")} SET post_count=? WHERE id=1',(counter,))
        w.execute(f'UPDATE {self.table("members")} SET post_count=post_count+1 WHERE id=?',(actor,))
        if self.semantic is not None:
            self.semantic.enqueue_in(w,identifier,counter)
        if reply_to is not None:
            w.execute(f'UPDATE {self.table("posts")} SET repost_count=repost_count+1 WHERE id=?',(reply_to,))
            self.engagement.update_in(w,reply_to)
        self.data.event_in(w,actor,'publish_post',identifier,tick)
        return ActionResult('completed',{'post_id':identifier})

    def execute_in(self,w,operation,actor,target,arguments,tick):
        if operation=='publish_post':
            if target!=actor:return ActionResult('rejected',{'reason':'participant_unavailable'})
            return self.publish_in(w,actor,arguments['content'],arguments.get('tags',[]),tick,arguments.get('reply_to'))
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
            self.engagement.update_in(w,target)
            notice='post_like'
            result=ActionResult('completed',{'changed':True})
        elif operation=='comment':
            content=arguments['content']
            if not self._content_valid(content):return ActionResult('rejected',{'reason':'content_length'})
            w.execute(f'INSERT INTO {self.table("replies")}(post,author,tick,body) VALUES(?,?,?,?)',(target,actor,tick,content.encode()))
            reply=w.query(f'SELECT max(id) FROM {self.table("replies")}')[0][0]
            w.execute(f'INSERT INTO {self.table("recent_interactions")}(actor,tick,post_ordinal,kind_order,source_id,post) VALUES(?,?,?,1,?,?)',(actor,tick,post_ordinal,reply,target))
            w.execute(f'UPDATE {self.table("posts")} SET reply_count=reply_count+1 WHERE id=?',(target,))
            self.engagement.update_in(w,target)
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
            result=self.publish_in(w,actor,content,json.loads(tags),tick,target)
            if result.status!='completed':return result
            notice='post_repost';data['repost_id']=result.value['post_id']
            data['commentary_preview']=commentary.strip() if len(commentary.strip())<=240 else commentary.strip()[:240].rstrip()+'...'
        else:raise ValueError(operation)
        self.data.event_in(w,actor,operation,target,tick)
        if author!=actor:self.notices.notify_in(w,author,notice,data,tick)
        return result

    def post_details(self,identifier):
        return self.store.read(lambda r:_post_details(r,self.name,identifier))

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

class Exposure(DomainService):
    def __init__(self,data):
        super().__init__(data)
        self._impressions={};self._recommended={}

    def _validate(self,actor,ids):
        def validate(r):
            if not self.data.member_in(r,actor):raise ValueError('unknown social member')
            for identifier in ids:
                if not r.query(f'SELECT 1 FROM {self.table("posts")} WHERE id=?',(identifier,)):raise ValueError('unknown presented post')
        self.store.read(validate)

    def set_recommended(self,actor,ids):
        ids=tuple(ids)
        self._validate(actor,ids)
        self._recommended[actor]=list(ids)

    def record(self,actor,ids,*,recommended=True):
        ids=tuple(ids)
        self._validate(actor,ids)
        for identifier in ids:self._impressions[identifier]=self._impressions.get(identifier,0)+1
        if recommended:self._recommended[actor]=list(ids)

    def flush(self):
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

class ActivePool(DomainService):
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

    def trending_ids(self,limit=2):
        return self.store.read(lambda r:[row[0] for row in r.query(f'SELECT id FROM {self.table("posts")} ORDER BY engagement DESC,created_tick DESC,id DESC LIMIT ?',(limit,))])
