"""共享环境中的轮次对话：顺序配对、消息事实与主体视图。"""
import time

from ..kernel.plugins import Plugin
from ..kernel.interaction import Action, ActionResult
from ..kernel.information_sql import SQLInformation, DatasetSpec, DocumentSpec, _quote


def _schedule(group):
    rotation=list(group)
    rounds=[]
    for _ in range(len(group)-1):
        rounds.append([(rotation[i],rotation[-1-i]) for i in range(len(group)//2)])
        rotation=[rotation[0],rotation[-1],*rotation[1:-1]]
    return rounds


def _schema(name):
    t=lambda suffix:_quote(name+'_'+suffix)
    return (
        f'CREATE TABLE {t("head")}(id INTEGER PRIMARY KEY,current_round INTEGER NOT NULL,total_rounds INTEGER NOT NULL,inbox_after INTEGER NOT NULL,duration INTEGER NOT NULL)',
        f'CREATE TABLE {t("members")}(id TEXT PRIMARY KEY NOT NULL,group_no INTEGER NOT NULL,ordinal INTEGER NOT NULL,partner TEXT,round INTEGER NOT NULL,active INTEGER NOT NULL,FOREIGN KEY(id) REFERENCES actors(id))',
        f'CREATE INDEX {t("group_order")} ON {t("members")}(group_no,ordinal)',
        f'CREATE TABLE {t("schedule")}(id INTEGER PRIMARY KEY,round INTEGER NOT NULL,group_no INTEGER NOT NULL,first TEXT NOT NULL,second TEXT NOT NULL)',
        f'CREATE INDEX {t("schedule_round")} ON {t("schedule")}(round,id)',
        f'CREATE INDEX {t("schedule_group")} ON {t("schedule")}(group_no,round,id)',
        f'CREATE TABLE {t("paired")}(first TEXT NOT NULL,second TEXT NOT NULL,PRIMARY KEY(first,second))',
        f'CREATE TABLE {t("partners")}(id INTEGER PRIMARY KEY,actor TEXT NOT NULL,partner TEXT NOT NULL,round INTEGER NOT NULL)',
        f'CREATE INDEX {t("partner_history")} ON {t("partners")}(actor,id)',
        f'CREATE TABLE {t("messages")}(id INTEGER PRIMARY KEY,sender TEXT NOT NULL,receiver TEXT NOT NULL,round INTEGER NOT NULL,timestamp REAL NOT NULL,body BLOB NOT NULL)',
        f'CREATE INDEX {t("inbox")} ON {t("messages")}(receiver,round,id)',
        f'CREATE INDEX {t("message_history")} ON {t("messages")}(receiver,id)',
        f'CREATE TABLE {t("counts")}(receiver TEXT NOT NULL,round INTEGER NOT NULL,total INTEGER NOT NULL,PRIMARY KEY(receiver,round))',
        f'CREATE INDEX {t("count_round")} ON {t("counts")}(round,receiver)',
    )


class RoundRobin:
    def __init__(self,name,store,actors,session_duration_minutes,clock):
        self.name,self.store,self.actors=name,store,actors
        self.session_duration_minutes=store.read(lambda r:r.query(f'SELECT duration FROM {self.table("head")} WHERE id=1')[0][0])
        self.clock=clock

    def table(self,suffix): return _quote(self.name+'_'+suffix)

    def _member(self,view,actor):
        rows=view.query(f'SELECT group_no,ordinal,partner,round,active FROM {self.table("members")} WHERE id=?',(actor,))
        if not rows: raise KeyError(actor)
        return rows[0]

    def pairing(self,actor):
        def read(r):
            group,_,partner,round_number,active=self._member(r,actor)
            total=r.query(f'SELECT total_rounds FROM {self.table("head")} WHERE id=1')[0][0]
            history=[row[0] for row in r.iter_query(f'SELECT partner FROM {self.table("partners")} WHERE actor=? ORDER BY id',(actor,))]
            return {'agent_id':actor,'group':group,'current_partner':partner,'current_round':round_number,
                    'total_rounds':total,'can_converse':bool(active),'partner_history':history,
                    'session_duration_minutes':self.session_duration_minutes}
        return self.store.read(read)

    def group_view(self,actor):
        def read(r):
            group,_,_,round_number,_=self._member(r,actor)
            members=[row[0] for row in r.iter_query(f'SELECT id FROM {self.table("members")} WHERE group_no=? ORDER BY ordinal',(group,))]
            upcoming=[{'round':number,'partner':b if a==actor else a} for number,a,b in r.iter_query(
                f'SELECT round,first,second FROM {self.table("schedule")} WHERE group_no=? AND round>=? ORDER BY round,id',(group,round_number)) if actor in (a,b)]
            return {'members':members,'upcoming':upcoming,'current_round':round_number}
        return self.store.read(read)

    def conversation_view(self, actor):
        def read(r):
            group,_,partner,number,active=self._member(r,actor)
            total,after=r.query(f'SELECT total_rounds,inbox_after FROM {self.table("head")} WHERE id=1')[0]
            history=[row[0] for row in r.iter_query(f'SELECT partner FROM {self.table("partners")} WHERE actor=? ORDER BY id',(actor,))]
            messages=[{'id':i,'sender':sender,'receiver':actor,'round':number,'timestamp':stamp,'content':body.decode()}
                      for i,sender,stamp,body in r.iter_query(f'SELECT id,sender,timestamp,body FROM {self.table("messages")} WHERE receiver=? AND round=? AND id>? ORDER BY id',(actor,number,after))]
            return {'actor':actor,'group':group,'current_partner':partner,'current_round':number,'total_rounds':total,
                    'can_converse':bool(active),'partner_history':history,'messages':messages,
                    'session_duration_minutes':self.session_duration_minutes,'revision':r.live_revision}
        return self.store.read(read)

    def initialize_round_messages(self, round_number):
        if type(round_number) is not int or round_number<1: raise ValueError('round must be positive')
        def write(w):
            w.execute(f'UPDATE {self.table("head")} SET inbox_after=(SELECT coalesce(max(id),0) FROM {self.table("messages")}) WHERE id=1')
            w.execute(f'UPDATE {self.table("counts")} SET total=0 WHERE round>0')
            return {'status':'initialized','round':round_number}
        return self.store.transaction(write)

    def start_round(self,round_number):
        def write(w):
            total=w.query(f'SELECT total_rounds FROM {self.table("head")} WHERE id=1')[0][0]
            if type(round_number) is not int or not 1<=round_number<=total:
                raise ValueError('round is outside the schedule')
            current=w.query(f'SELECT current_round FROM {self.table("head")} WHERE id=1')[0][0]
            if current==round_number and w.query(f'SELECT 1 FROM {self.table("members")} WHERE active=1 LIMIT 1'):
                return {'round':round_number,'pairs':[],'successful_pairs':0}
            w.execute(f'UPDATE {self.table("head")} SET current_round=? WHERE id=1',(round_number,))
            w.execute(f'UPDATE {self.table("members")} SET partner=NULL,round=?,active=0',(round_number,))
            pairs=[]
            for a,b in w.iter_query(f'SELECT first,second FROM {self.table("schedule")} WHERE round=? ORDER BY id',(round_number,)):
                canonical=tuple(sorted((a,b)))
                if w.query(f'SELECT 1 FROM {self.table("paired")} WHERE first=? AND second=?',canonical): continue
                w.execute(f'INSERT INTO {self.table("paired")} VALUES(?,?)',canonical)
                for actor,partner in ((a,b),(b,a)):
                    w.execute(f'UPDATE {self.table("members")} SET partner=?,active=1 WHERE id=?',(partner,actor))
                    w.execute(f'INSERT INTO {self.table("partners")}(actor,partner,round) VALUES(?,?,?)',(actor,partner,round_number))
                pairs.append((a,b))
            return {'round':round_number,'pairs':pairs,'successful_pairs':len(pairs)}
        return self.store.transaction(write)

    def advance_round(self):
        def write(w):
            current,total=w.query(f'SELECT current_round,total_rounds FROM {self.table("head")} WHERE id=1')[0]
            if not total: return {'status':'idle'}
            if current>=total: return {'status':'completed'}
            current+=1
            w.execute(f'UPDATE {self.table("head")} SET current_round=? WHERE id=1',(current,))
            w.execute(f'UPDATE {self.table("members")} SET partner=NULL,round=?,active=0',(current,))
            pairs=list(w.iter_query(f'SELECT group_no,first,second FROM {self.table("schedule")} WHERE round=? ORDER BY id',(current,)))
            return {'status':'advanced','new_round':current,'pairings_available':pairs}
        return self.store.transaction(write)

    def _send(self,actor,content,broadcast):
        if not content.strip(): return ActionResult('rejected',{'reason':'empty_content'})
        timestamp=self.clock()
        def write(w):
            group,_,partner,current,active=self._member(w,actor)
            if not active or (not broadcast and not partner): return ActionResult('rejected',{'reason':'no_active_conversation'})
            receivers=([row[0] for row in w.iter_query(f'SELECT id FROM {self.table("members")} WHERE group_no=? AND id<>? ORDER BY ordinal',(group,actor))]
                       if broadcast else [partner])
            delivered=[]
            for receiver in receivers:
                w.execute(f'INSERT INTO {self.table("messages")}(sender,receiver,round,timestamp,body) VALUES(?,?,?,?,?)',(actor,receiver,current,timestamp,content.encode()))
                message_id=w.query(f'SELECT max(id) FROM {self.table("messages")}')[0][0]
                for counted_round in (0,current):
                    w.execute(f'INSERT INTO {self.table("counts")} VALUES(?,?,1) ON CONFLICT(receiver,round) DO UPDATE SET total=total+1',(receiver,counted_round))
                delivered.append({'receiver':receiver,'message_id':message_id})
            value={'round':current,'delivered':delivered}
            if not broadcast: value.update(sent_to=partner,message_id=delivered[0]['message_id'])
            return ActionResult('completed',value)
        return self.store.transaction(write)

    def register(self,information,actions):
        dependencies=(self.name+'_members',self.name+'_head')
        def own(scope,target):
            return target.key==scope.actor and self.store.read(lambda r:bool(r.query(f'SELECT 1 FROM {self.table("members")} WHERE id=?',(scope.actor,))))
        def available(scope,target):
            return own(scope,target) and self.pairing(scope.actor)['can_converse']
        content={'type':'object','properties':{'content':{'type':'string'}},'required':['content'],'additionalProperties':False}
        for operation,broadcast in (('send_message_to_partner',False),('broadcast_to_group',True)):
            actions.register(Action(self.name+'.'+operation,(self.name,'participants'),'发送完整消息给当前伙伴。' if not broadcast else '向当前小组其他成员广播完整消息。',content,
                lambda scope,target,args,broadcast=broadcast:self._send(scope.actor,args['content'],broadcast),available=available),dependencies=dependencies)
        def mark(scope,target,args):
            self.actors.set_state(scope.actor,'conversation_marker',args['marker'])
            return ActionResult('completed',{'marker':args['marker'],**self.pairing(scope.actor)})
        actions.register(Action(self.name+'.mark_conversation_participant',(self.name,'participants'),'保存本主体的对话参与标记。',
            {'type':'object','properties':{'marker':{'type':'string'}},'required':['marker'],'additionalProperties':False},mark,available=own),dependencies=dependencies)
        information.mount('/'+self.name,round_robin_information(self.store,self.name))


def round_robin_information(reader,name='conversation'):
    """按主体读取既有资料；与运行机制使用同一组 SQL 路由。"""
    table=lambda suffix:_quote(name+'_'+suffix)
    current=f'(SELECT current_round FROM {table("head")} WHERE id=1)'
    def inbox(scope): return ('receiver=? AND round='+current+f' AND id>(SELECT inbox_after FROM {table("head")} WHERE id=1)',(scope.actor,))
    def history(scope): return ('receiver=?',(scope.actor,))
    def counter(round_expression):
        return lambda scope:(f'SELECT coalesce(sum(total),0) FROM {table("counts")} WHERE receiver=? AND round='+round_expression,(scope.actor,))
    columns=('id','sender','receiver','round','timestamp')
    return SQLInformation(name,reader,{
        'participants':DatasetSpec(name+'_members','id',('id','group_no','partner','round','active'),authorize=lambda scope:('id=?',(scope.actor,))),
        'messages':DatasetSpec(name+'_messages','id',columns,authorize=inbox,base_count=counter(current),dependencies=(name+'_head',name+'_counts')),
        'history':DatasetSpec(name+'_messages','id',columns,authorize=history,base_count=counter('0'),dependencies=(name+'_counts',)),
        'content':DocumentSpec(name+'_messages','id','body',authorize=history),
    })


def round_robin_plugin(members, *, group_size, name='conversation', session_duration_minutes=10,
                       clock=time.time, storage='storage', actors='actors', interaction='interaction'):
    members=tuple(members)
    if type(group_size) is not int or group_size<2 or group_size>20 or group_size%2 or len(members)%group_size or len(set(members))!=len(members):
        raise ValueError('members require distinct identities in complete even groups of 2 to 20')
    if not 1<=session_duration_minutes<=120: raise ValueError('invalid session duration')
    if not name or '/' in name: raise ValueError('mechanism name must be one path segment')
    t=lambda suffix:_quote(name+'_'+suffix)
    def initialize(w):
        total=group_size-1 if members else 0
        w.execute(f'INSERT INTO {t("head")} VALUES(1,?,?,0,?)',(1 if total else 0,total,session_duration_minutes))
        for index,actor in enumerate(members):
            w.execute(f'INSERT INTO {t("members")} VALUES(?,?,?,NULL,?,0)',(actor,index//group_size,index,1 if total else 0))
        for group_no,start in enumerate(range(0,len(members),group_size)):
            for number,pairs in enumerate(_schedule(members[start:start+group_size]),1):
                for a,b in pairs: w.execute(f'INSERT INTO {t("schedule")}(round,group_no,first,second) VALUES(?,?,?,?)',(number,group_no,a,b))
    def install(ctx):
        mechanism=RoundRobin(name,ctx.require(storage,'store'),ctx.require(actors,'actors'),session_duration_minutes,clock)
        mechanism.register(ctx.require(interaction,'information'),ctx.require(interaction,'actions'))
        ctx.provide('mechanism',mechanism)
    return Plugin(name,(storage,actors,interaction),install,schema=_schema(name),initialize=initialize)
