"""小型记录插件：事实追加与当前投影分表，保留类型、顺序与完整值。"""
import json
from society0.kernel.plugins import Plugin
from society0.kernel.interaction import Action, ActionResult
from society0.kernel._json_chunks import raw_chunks

SCHEMA = (
    'CREATE TABLE record_settings(id INTEGER PRIMARY KEY,owner TEXT NOT NULL)',
    'CREATE TABLE record_facts(ordinal INTEGER PRIMARY KEY,kind TEXT NOT NULL,key TEXT NOT NULL,body TEXT NOT NULL,UNIQUE(kind,key))',
    'CREATE TABLE record_projection(kind TEXT NOT NULL,key TEXT NOT NULL,body TEXT NOT NULL,PRIMARY KEY(kind,key))',
)


def _key(value):
    if type(value) is int:
        return 'int', str(value)
    if type(value) is str:
        return 'str', value
    raise TypeError('record key must be an integer or string')


def _body(value):
    return b''.join(raw_chunks(value)).decode('utf-8')


class Records:
    def __init__(self,store):
        self.store=store

    @staticmethod
    def append_to(writer,key,value):
        rows=writer.query('INSERT INTO record_facts(kind,key,body) VALUES(?,?,?) ON CONFLICT(kind,key) DO NOTHING RETURNING ordinal',(*_key(key),_body(value)))
        return bool(rows)

    @staticmethod
    def project_to(writer,key,value):
        writer.execute('INSERT INTO record_projection VALUES(?,?,?) ON CONFLICT(kind,key) DO UPDATE SET body=excluded.body',(*_key(key),_body(value)))

    def append(self,key,value):
        return self.store.transaction(lambda writer:self.append_to(writer,key,value))

    def project(self,key,value):
        return self.store.transaction(lambda writer:self.project_to(writer,key,value))

    def facts(self):
        return self.store.read(lambda reader:[
            (int(key) if kind=='int' else key,json.loads(body))
            for kind,key,body in reader.iter_query('SELECT kind,key,body FROM record_facts ORDER BY ordinal')])

    def projection(self,key):
        return self.store.read(lambda reader:json.loads(reader.query(
            'SELECT body FROM record_projection WHERE kind=? AND key=?',_key(key))[0][0]))

    def owns(self,scope,target):
        return target.key=='main' and self.store.read(lambda reader:
            reader.query('SELECT owner FROM record_settings WHERE id=1')[0][0])==scope.actor


def record_plugin(*,owner):
    def initialize(writer):
        writer.execute('INSERT INTO record_settings VALUES(1,?)',(owner,))
    def install(context):
        records=Records(context.require('storage','store'))
        actions=context.require('interaction','actions')
        schema={'type':'object','properties':{'key':{'type':['integer','string']},'value':{}},
                'required':['key','value'],'additionalProperties':False}
        def append(scope,target,args):
            if not records.owns(scope,target):return ActionResult('rejected')
            return ActionResult('completed' if records.append(args['key'],args['value']) else 'rejected')
        def project(scope,target,args):
            if not records.owns(scope,target):return ActionResult('rejected')
            records.project(args['key'],args['value']);return ActionResult('completed')
        for name,handler in (('append',append),('project',project)):
            actions.register(Action('records.'+name,('records','ledger'),name,schema,handler,
                available=records.owns),dependencies=('record_settings','record_facts','record_projection'))
        context.provide('records',records)
    return Plugin('records',('storage','interaction'),install,schema=SCHEMA,initialize=initialize)
