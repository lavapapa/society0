"""主体权威目录；驱动按实际激活加载，私有工作区按主体保存。"""
from collections.abc import Mapping
from dataclasses import dataclass, field
import json

from .interaction import Page, Ref
from .plugins import Plugin
from .runtime import Actor

ACTOR_SCHEMA = (
    'CREATE TABLE actors(ordinal INTEGER PRIMARY KEY,id TEXT NOT NULL UNIQUE,driver TEXT NOT NULL,active INTEGER NOT NULL,data_revision INTEGER NOT NULL DEFAULT 0)',
    'CREATE TABLE actor_personas(actor TEXT PRIMARY KEY NOT NULL,body TEXT NOT NULL,FOREIGN KEY(actor) REFERENCES actors(id))',
    'CREATE TABLE actor_configs(actor TEXT PRIMARY KEY NOT NULL,body TEXT NOT NULL,FOREIGN KEY(actor) REFERENCES actors(id))',
    'CREATE TABLE actor_state(actor TEXT NOT NULL,key TEXT NOT NULL,ordinal INTEGER NOT NULL,value TEXT NOT NULL,PRIMARY KEY(actor,key),UNIQUE(actor,ordinal),FOREIGN KEY(actor) REFERENCES actors(id))',
    'CREATE TABLE actor_selection(kind INTEGER NOT NULL,role TEXT NOT NULL,active INTEGER NOT NULL,ordinal INTEGER NOT NULL,actor TEXT NOT NULL,PRIMARY KEY(kind,role,active,ordinal),FOREIGN KEY(actor) REFERENCES actors(id))',
    'CREATE INDEX actor_selection_order ON actor_selection(kind,role,ordinal)',
    'CREATE INDEX actor_selection_actor ON actor_selection(actor)',
    'CREATE TABLE actor_counts(kind INTEGER NOT NULL,role TEXT NOT NULL,active INTEGER NOT NULL,total INTEGER NOT NULL,PRIMARY KEY(kind,role,active))',
)


def _json(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)


@dataclass(frozen=True)
class ActorRecord:
    id: str
    driver: str
    persona: object = ''
    state: object = field(default_factory=dict)
    config: object = field(default_factory=dict)
    roles: tuple[str,...] = ()
    active: bool = True

    def __post_init__(self):
        object.__setattr__(self,'roles',tuple(self.roles))
        if type(self.active) is not bool:
            raise TypeError('active must be boolean')


@dataclass(frozen=True)
class ActorRecordView:
    id: str
    driver: str
    active: bool
    roles: tuple
    revision: int
    _store: object = field(repr=False,compare=False)

    def _read(self,callback):
        def read(view):
            rows=view.query('SELECT data_revision FROM actors WHERE id=?',(self.id,))
            if not rows or rows[0][0]!=self.revision:
                raise ValueError('actor record revision changed; acquire a new view')
            return callback(view)
        return self._store.read(read)

    @property
    def persona(self):
        return self._read(lambda r:json.loads(r.query('SELECT body FROM actor_personas WHERE actor=?',(self.id,))[0][0]))

    @property
    def config(self):
        return self._read(lambda r:json.loads(r.query('SELECT body FROM actor_configs WHERE actor=?',(self.id,))[0][0]))

    @property
    def state(self):
        return self.state_values()

    def state_values(self,keys=None):
        keys=None if keys is None else tuple(dict.fromkeys(keys))
        def read(r):
            where='actor=?';bindings=(self.id,)
            if keys is not None:
                if not keys:return {}
                where+=' AND key IN ('+','.join('?' for _ in keys)+')';bindings+=keys
            return {key:json.loads(value) for key,value in r.iter_query('SELECT key,value FROM actor_state WHERE '+where+' ORDER BY ordinal',bindings)}
        return self._read(read)


def _state_rows(actor, state):
    if not isinstance(state, dict) or any(not isinstance(key,str) for key in state):
        raise TypeError('subjective state requires a mapping with string keys')
    return ((actor,key,ordinal,_json(value)) for ordinal,(key,value) in enumerate(state.items(),1))


def _selection(writer, actor, ordinal, active, roles):
    for kind,role in ((0,''), *((1,role) for role in roles)):
        writer.execute('INSERT INTO actor_selection VALUES(?,?,?,?,?)',(kind,role,int(active),ordinal,actor))
        writer.execute('INSERT INTO actor_counts VALUES(?,?,?,1) ON CONFLICT(kind,role,active) DO UPDATE SET total=total+1',(kind,role,int(active)))


def _insert(writer, record):
    writer.execute('INSERT INTO actors(id,driver,active) VALUES(?,?,?)',(record.id,record.driver,int(record.active)))
    ordinal=writer.query('SELECT ordinal FROM actors WHERE id=?',(record.id,))[0][0]
    writer.execute('INSERT INTO actor_personas VALUES(?,?)',(record.id,_json(record.persona)))
    writer.execute('INSERT INTO actor_configs VALUES(?,?)',(record.id,_json(record.config)))
    writer.executemany('INSERT INTO actor_state VALUES(?,?,?,?)',_state_rows(record.id,record.state))
    _selection(writer,record.id,ordinal,record.active,record.roles)


class ActorDirectory:
    """主体数据服务；driver 字段保存标签，不取得驱动资源。"""
    def __init__(self, store):
        self.store=store

    def get_record(self, actor):
        def read(view):
            rows=view.query('SELECT driver,active FROM actors WHERE id=?',(actor,))
            if not rows: raise KeyError(actor)
            driver,active=rows[0]
            persona=json.loads(view.query('SELECT body FROM actor_personas WHERE actor=?',(actor,))[0][0])
            config=json.loads(view.query('SELECT body FROM actor_configs WHERE actor=?',(actor,))[0][0])
            state={key:json.loads(value) for key,value in view.iter_query('SELECT key,value FROM actor_state WHERE actor=? ORDER BY ordinal',(actor,))}
            roles=tuple(row[0] for row in view.iter_query('SELECT role FROM actor_selection WHERE actor=? AND kind=1 ORDER BY role',(actor,)))
            return ActorRecord(actor,driver,persona,state,config,roles,bool(active))
        return self.store.read(read)

    def view(self,actor):
        def read(r):
            rows=r.query('SELECT driver,active,data_revision FROM actors WHERE id=?',(actor,))
            if not rows:raise KeyError(actor)
            driver,active,revision=rows[0]
            roles=tuple(row[0] for row in r.iter_query('SELECT role FROM actor_selection WHERE actor=? AND kind=1 ORDER BY role',(actor,)))
            return ActorRecordView(actor,driver,bool(active),roles,revision,self.store)
        return self.store.read(read)

    def __len__(self):
        return self.store.read(lambda r:r.query("SELECT coalesce(sum(total),0) FROM actor_counts WHERE kind=0 AND role=''")[0][0])

    def __iter__(self):
        ordinal=0
        while True:
            rows=self.store.read(lambda r:r.query('SELECT ordinal,id FROM actors WHERE ordinal>? ORDER BY ordinal LIMIT 128',(ordinal,),max_rows=128))
            if not rows: return
            for ordinal,actor in rows: yield actor

    def add(self, record):
        return self.store.transaction(lambda w:_insert(w,record))

    def update(self, actor, **changes):
        allowed={'driver','persona','state','config','active','roles'}
        if not changes.keys() <= allowed: raise TypeError('unknown actor field')
        if 'active' in changes and type(changes['active']) is not bool: raise TypeError('active must be boolean')
        def write(w):
            rows=w.query('SELECT ordinal,active FROM actors WHERE id=?',(actor,))
            if not rows: raise KeyError(actor)
            ordinal,active=rows[0]
            if changes:w.execute('UPDATE actors SET data_revision=data_revision+1 WHERE id=?',(actor,))
            if 'driver' in changes: w.execute('UPDATE actors SET driver=? WHERE id=?',(changes['driver'],actor))
            for key,table in (('persona','actor_personas'),('config','actor_configs')):
                if key in changes: w.execute('UPDATE '+table+' SET body=? WHERE actor=?',(_json(changes[key]),actor))
            if 'state' in changes:
                w.execute('DELETE FROM actor_state WHERE actor=?',(actor,))
                w.executemany('INSERT INTO actor_state VALUES(?,?,?,?)',_state_rows(actor,changes['state']))
            if 'active' in changes or 'roles' in changes:
                previous=list(w.iter_query('SELECT kind,role,active FROM actor_selection WHERE actor=?',(actor,)))
                roles=changes.get('roles',tuple(role for kind,role,_ in previous if kind==1))
                for kind,role,prior_active in previous:
                    w.execute('UPDATE actor_counts SET total=total-1 WHERE kind=? AND role=? AND active=?',(kind,role,prior_active))
                w.execute('DELETE FROM actor_selection WHERE actor=?',(actor,))
                active=changes.get('active',bool(active))
                w.execute('UPDATE actors SET active=? WHERE id=?',(int(active),actor))
                _selection(w,actor,ordinal,active,roles)
        self.store.transaction(write)

    def set_state(self, actor, key, value):
        if not isinstance(key,str): raise TypeError('subjective state key must be a string')
        def write(w):
            w.execute('INSERT INTO actor_state VALUES(?,?,(SELECT coalesce(max(ordinal),0)+1 FROM actor_state WHERE actor=?),?) ON CONFLICT(actor,key) DO UPDATE SET value=excluded.value',(actor,key,actor,_json(value)))
            w.execute('UPDATE actors SET data_revision=data_revision+1 WHERE id=?',(actor,))
        self.store.transaction(write)

    def deactivate(self,actor):
        self.update(actor,active=False)

    def select(self, *, role=None, active=True, limit=100, cursor=None):
        if type(limit) is not int or limit<1: raise ValueError('limit must be positive')
        if active is not None and type(active) is not bool: raise TypeError('active must be boolean or None')
        identity=[self.store.run_id,role,active]
        def read(r):
            revision=r.revision_for(('actor_selection','actor_counts'))
            after=0
            if cursor is not None:
                if cursor['identity']!=identity or cursor['revision']!=revision: raise ValueError('actor cursor mismatch')
                after=cursor['after']
                if type(after) is not int or after<0: raise ValueError('invalid actor cursor')
            where='kind=? AND role=?'
            params=[int(role is not None),role if role is not None else '']
            if active is not None:
                where+=' AND active=?'; params.append(int(active))
            total=r.query('SELECT coalesce(sum(total),0) FROM actor_counts WHERE '+where,params)[0][0]
            rows=r.query('SELECT ordinal,actor FROM actor_selection WHERE '+where+' AND ordinal>? ORDER BY ordinal LIMIT ?',(*params,after,limit+1),max_rows=limit+1)
            page=rows[:limit]
            next_cursor={'identity':identity,'revision':revision,'after':page[-1][0]} if len(rows)>limit else None
            return Page([row[1] for row in page],total,next_cursor,revision)
        return self.store.read(read)


class ActorStore(ActorDirectory, Mapping):
    """运行映射绑定目录与驱动；数据服务可独立于此映射安装。"""
    def __init__(self, directory, drivers):
        super().__init__(directory.store)
        self.drivers=dict(drivers)
        unresolved=self.store.read(lambda view:[name for (name,) in view.iter_query('SELECT DISTINCT driver FROM actors') if name not in self.drivers])
        if unresolved: raise ValueError('unresolved actor drivers: '+', '.join(unresolved))

    def __getitem__(self, actor):
        record=self.view(actor)
        return Actor(actor,self.drivers[record.driver](record),Ref('actors','state',actor),record)

    def add(self, record):
        if record.driver not in self.drivers: raise KeyError(record.driver)
        return super().add(record)

    def update(self, actor, **changes):
        if 'driver' in changes and changes['driver'] not in self.drivers: raise KeyError(changes['driver'])
        return super().update(actor,**changes)


def actor_data_plugin(*, records=(), name='actors.data', storage='storage'):
    """独立主体目录；保持原有权威表与字段。"""
    def initialize(writer):
        for record in records: _insert(writer,record)
    def install(ctx):
        ctx.provide('directory',ActorDirectory(ctx.require(storage,'store')))
    return Plugin(name,(storage,),install,schema=ACTOR_SCHEMA,initialize=initialize)


def actor_plugin(drivers, *, records=(), name='actors', storage='storage', requires=(), driver_factory=None, data=None):
    """默认包含数据目录；显式 data 引用允许复用已安装的目录。"""
    includes=()
    if data is None:
        data=(name+'.data','directory')
        includes=(actor_data_plugin(records=records,name=data[0],storage=storage),)
    elif records:
        raise ValueError('records belong to actor_data_plugin when data is explicit')
    declared=dict(drivers) if driver_factory is None else dict.fromkeys(drivers)
    references=tuple(value for value in declared.values() if isinstance(value,tuple))
    def install(ctx):
        configured={name:ctx.require(*factory) if isinstance(factory,tuple) else factory for name,factory in declared.items()} if driver_factory is None else dict(driver_factory(ctx))
        if configured.keys()!=declared.keys():
            raise ValueError('driver names must match the declared names')
        ctx.provide('actors',ActorStore(ctx.require(*data),configured))
    return Plugin(name,tuple(dict.fromkeys((data[0],*requires,*(item[0] for item in references)))),install,includes=includes)
