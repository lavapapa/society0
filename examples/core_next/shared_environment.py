"""两个机制、两个规则主体共享 SQL 世界，经 shell 查询和行动后恢复完整步骤。"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import shlex

from society0.kernel.plugins import Plugin, PluginHost
from society0.kernel.interaction import Action, ActionResult, Actions, Information
from society0.kernel.information_sql import DatasetSpec, DocumentSpec, SQLInformation
from society0.kernel.runtime import Actor, DriverResult, Phase, runtime_plugin
from society0.kernel.shell import ShellSession
from society0.kernel.storage import StageReader, StageStore


def argument(value):
    return shlex.quote(json.dumps(value, ensure_ascii=False))


async def demonstrate(output: Path, *, history=2000):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    stores, evidence = [], []
    interfaces = {}

    def storage(ctx):
        # BLOB 权威正文按字节保存；数据集的热列与冷正文分表。
        def init(w):
            w.executemany('INSERT INTO orders VALUES(?,?,?,?,?)',
                ((i, 'alice', 0, i % 17 + 1, 'historical') for i in range(1, history + 1)))
            w.execute('INSERT INTO orders VALUES(?,?,?,?,?)', (history + 1, 'alice', 1, 12, 'available'))
            w.execute("INSERT INTO counts VALUES('alice',1)")
            w.execute('INSERT INTO documents VALUES(1,?)', (('共享制度原文🙂\n' * 100000).encode(),))
        store = StageStore.create(output / 'run', [
            'CREATE TABLE orders(id INTEGER PRIMARY KEY,owner TEXT NOT NULL,active INTEGER NOT NULL,price INTEGER NOT NULL,status TEXT NOT NULL)',
            'CREATE INDEX orders_active ON orders(owner,active,id)',
            'CREATE INDEX orders_history ON orders(owner,id)',
            'CREATE TABLE counts(owner TEXT PRIMARY KEY NOT NULL,total INTEGER NOT NULL)',
            'CREATE TABLE messages(id INTEGER PRIMARY KEY,recipient TEXT NOT NULL,body TEXT NOT NULL)',
            'CREATE INDEX messages_recipient ON messages(recipient,id)',
            'CREATE TABLE documents(id INTEGER PRIMARY KEY,body BLOB NOT NULL)',
            'CREATE TABLE analysis(actor TEXT PRIMARY KEY NOT NULL,manifest TEXT NOT NULL)',
        ], initialize=init)
        stores.append(store)
        ctx.provide('store', store)
        ctx.on_close(store.close)

    def interaction(ctx):
        # 路由层目录可发现；行权限由 provider 的 SQL 谓词限制。
        interfaces['information'] = Information(lambda *args: True)
        ctx.provide('information', interfaces['information'])
        ctx.provide('actions', Actions(lambda scope, op, ref: ref.namespace == 'world'))

    def market(ctx):
        store = ctx.require('storage', 'store')
        actions = ctx.require('interaction', 'actions')
        def buy(scope, target, args):
            def write(w):
                rows = w.query('SELECT active,price FROM orders WHERE id=? AND owner=?', (int(target.key), scope.actor))
                if not rows or not rows[0][0]:
                    return ActionResult('rejected', {'reason': 'unavailable'})
                w.execute("UPDATE orders SET active=0,status='purchased' WHERE id=?", (int(target.key),))
                w.execute('UPDATE counts SET total=total-1 WHERE owner=?', (scope.actor,))
                # 同一规范事务同时写订单事实与跨机制通知。
                w.execute('INSERT INTO messages(recipient,body) VALUES(?,?)', ('bob', f'{scope.actor}购买订单{target.key}，价格{rows[0][1]}'))
                return ActionResult('completed', {'price': rows[0][1]})
            return store.transaction(write)
        def available(scope, target):
            return bool(store.read(lambda r: r.query('SELECT 1 FROM orders WHERE id=? AND owner=? AND active=1', (int(target.key),scope.actor))))
        actions.register(Action('market.buy', ('world', 'orders'), '购买可用订单',
                                {'type':'object','properties':{},'required':[],'additionalProperties':False},
                                buy, available=available, terminal=True, strict=True, tags=('trade',)),dependencies=('orders',))

    def messaging(ctx):
        store = ctx.require('storage', 'store')
        recipient=lambda s:('recipient=?',(s.actor,))
        provider = SQLInformation('world', StageReader(store.path), {
            'orders': DatasetSpec('orders','id',('id','price','status'),
                authorize=lambda s:('owner=? AND active=1',(s.actor,)),
                base_count=lambda s:('SELECT total FROM counts WHERE owner=?',(s.actor,)),dependencies=('counts',)),
            'history': DatasetSpec('orders','id',('id','price','status'),authorize=lambda s:('owner=?',(s.actor,))),
            'messages': DatasetSpec('messages','id',('id','body'),authorize=recipient,documents=(('body','message_content'),)),
            'message_content': DocumentSpec('messages','id','body',authorize=recipient),
            'documents': DocumentSpec('documents','id','body'),
        })
        ctx.require('interaction','information').mount('/world',provider)

    class RuleDriver:
        async def run(self, session):
            shell = ShellSession(session.scope, interfaces['information'],
                                 bound_actions=session.actions, result_dir=output / 'shell-results')
            results = []
            try:
                if session.actor.id == 'alice':
                    results.append(await shell.execute('data read /world/world/documents/1 ' + argument({'size':64})))
                    results.append(await shell.execute('data query /world/world/history ' + argument({'fields':['id','price'],'limit':5,'sample_seed':7})))
                    listing = await shell.execute('data query /world/world/orders')
                    results.append(listing)
                    target = json.loads(listing.stdout)['items'][0]['ref']
                    results.append(await shell.execute('mkdir -p /workspace; data query /world/world/orders | jq "[.items[].price] | add" > /workspace/total; cat /workspace/total'))
                    found = await shell.execute('action find ' + argument({'target':target}))
                    results.append(found)
                    name = json.loads(found.stdout)['items'][0]['name']
                    results.append(await shell.execute('action describe ' + argument({'target':target,'name':name})))
                    results.append(await shell.execute('action invoke ' + argument({'target':target,'name':name,'arguments':{}})))
                else:
                    results.append(await shell.execute('data query /world/world/messages'))
                    message_path=json.loads(results[-1].stdout)['items'][0]['body']['logical_path']
                    results.append(await shell.execute('data read '+message_path))
                    assert 'alice购买订单' in results[-1].stdout
                evidence.append({'actor':session.actor.id,'commands':[asdict(item) for item in results]})
                # 由 Runtime 登记本完整步骤依赖的实际工件。
                artifact_map = {}
                for result in results:
                    for ref in (result.stdout_ref,result.stderr_ref,*result.receipts):
                        with (output / 'shell-results' / ref).open('rb') as stream:
                            artifact_map[ref] = session.prepare_artifact(iter(lambda:stream.read(65536),b''))
                workspace = session.prepare_artifact([shell.snapshot()])
                manifest = session.prepare_artifact([json.dumps({'results':artifact_map,'workspace':workspace}).encode()])
                stores[0].transaction(lambda w:w.execute('INSERT INTO analysis VALUES(?,?)',(session.actor.id,manifest)))
                return DriverResult('completed')
            finally:
                await shell.aclose()

    def phase(ctx):
        ctx.activate('alice')
        ctx.activate('bob')
    async with PluginHost([
        Plugin('storage',install=storage), Plugin('interaction',install=interaction),
        Plugin('market',('storage','interaction'),market),
        Plugin('messaging',('storage','interaction'),messaging),
        runtime_plugin([Actor('alice',RuleDriver()),Actor('bob',RuleDriver())],
            information=('interaction','information'),actions=('interaction','actions'),store=('storage','store')),
    ]) as host:
        descriptor = await host.service('runtime','runtime').run_step(1,0,[Phase('trade',phase)])
        original = stores[0].read(lambda r:r.query('SELECT id,owner,active,price,status FROM orders ORDER BY id',max_rows=history+1))
    with StageStore.restore(output / 'run',output / 'restored',step=1) as restored:
        assert restored.read(lambda r:r.query('SELECT id,owner,active,price,status FROM orders ORDER BY id',max_rows=history+1)) == original
        messages = restored.read(lambda r:r.query('SELECT recipient,body FROM messages ORDER BY id'))
        assert len(messages)==1 and messages[0][0]=='bob'
        manifests = restored.read(lambda r:r.query('SELECT actor,manifest FROM analysis ORDER BY actor'))
        for actor, path in manifests:
            manifest = json.loads((restored.path/path).read_bytes())
            assert all((restored.path/ref).is_file() for ref in manifest['results'].values())
            if actor == 'alice':
                from society0.kernel.interaction import InteractionScope, Moment
                with InteractionScope(actor,Moment(1,'analysis')) as scope:
                    private = ShellSession(scope,Information(lambda *args:False),Actions(lambda *args:False),
                        result_dir=output/'restored-shell',workspace_snapshot=(restored.path/manifest['workspace']).read_bytes())
                    try: assert (await private.execute('cat /workspace/total')).stdout=='12\n'
                    finally: await private.aclose()
    report={'history_records':history,'complete_step':descriptor['step'],'restored_equal':True,
            'artifact_count':len(descriptor['artifacts']),'actors':evidence}
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('output',type=Path)
    parser.add_argument('--history',type=int,default=2000)
    args=parser.parse_args()
    result=asyncio.run(demonstrate(args.output,history=args.history))
    print(json.dumps({key:value for key,value in result.items() if key!='actors'},ensure_ascii=False))
