"""声明驱动的共享运行构建；插件拥有自己的静态结构。"""
import pytest
from society0.kernel.plugins import Plugin
from society0.kernel.composition import compose


@pytest.mark.asyncio
async def test_plugin_schemas_initialize_in_dependency_order_and_share_store(tmp_path):
    calls=[]
    def initialize_a(w):
        calls.append('a')
        w.execute('INSERT INTO a VALUES(1,7)')
    def initialize_b(w):
        calls.append('b')
        w.execute('INSERT INTO b SELECT id,value+1 FROM a')
    def install_a(ctx):
        store=ctx.require('storage','store')
        ctx.provide('store',store)
        ctx.on_close(lambda:calls.append('close a'))
    def install_b(ctx):
        assert ctx.require('a','store') is ctx.require('storage','store')
        ctx.on_close(lambda:calls.append('close b'))
    a=Plugin('a',('storage',),install_a,schema=('CREATE TABLE a(id INTEGER PRIMARY KEY,value INTEGER)',),initialize=initialize_a)
    b=Plugin('b',('storage','a'),install_b,schema=('CREATE TABLE b(id INTEGER PRIMARY KEY,value INTEGER)',),initialize=initialize_b)
    async with compose(tmp_path/'run',[b,a]) as host:
        store=host.service('storage','store')
        assert store.read(lambda r:r.query('SELECT * FROM b'))==[(1,8)]
        assert calls==['a','b']
    assert calls==['a','b','close b','close a']
    with pytest.raises(RuntimeError): store.read(lambda r:None)


@pytest.mark.asyncio
async def test_bad_dependencies_and_initialization_never_publish_partial_run(tmp_path):
    target=tmp_path/'run'
    with pytest.raises(ValueError,match='missing'):
        async with compose(target,[Plugin('a',('missing',))]): pass
    assert not target.exists()
    def fail(w):
        w.execute('INSERT INTO a VALUES(1)')
        raise RuntimeError('initialize failed')
    with pytest.raises(RuntimeError,match='initialize failed'):
        async with compose(target,[Plugin('a',schema=('CREATE TABLE a(id INTEGER PRIMARY KEY)',),initialize=fail)]): pass
    assert not target.exists()


@pytest.mark.asyncio
async def test_pure_host_schema_declarations_are_immutable(tmp_path):
    ddl=['CREATE TABLE a(id INTEGER PRIMARY KEY)']
    plugin=Plugin('a',schema=ddl)
    ddl.clear()
    async with compose(tmp_path/'run',[plugin]) as host:
        assert host.service('storage','store').read(lambda r:r.query('SELECT count(*) FROM a'))==[(0,)]


@pytest.mark.asyncio
async def test_restore_keeps_state_without_reinitializing_and_checks_schema_first(tmp_path):
    calls=[]
    def initialize(w):
        calls.append('initialized')
        w.execute('INSERT INTO a VALUES(1,2)')
    plugin=Plugin('a',schema=('CREATE TABLE a(id INTEGER PRIMARY KEY,value INTEGER)',),initialize=initialize)
    async with compose(tmp_path/'source',[plugin]) as host:
        store=host.service('storage','store')
        store.transaction(lambda w:w.execute('UPDATE a SET value=3 WHERE id=1'))
        store.complete(1)
    async with compose(tmp_path/'restored',[plugin],source=tmp_path/'source',step=1) as host:
        assert host.service('storage','store').read(lambda r:r.query('SELECT value FROM a'))==[(3,)]
    assert calls==['initialized']
    with pytest.raises(Exception,match='schema'):
        async with compose(tmp_path/'bad',[Plugin('a',schema=('CREATE TABLE a(id INTEGER PRIMARY KEY)',))],source=tmp_path/'source'): pass
    assert not (tmp_path/'bad').exists()


@pytest.mark.asyncio
async def test_install_failure_closes_resources_and_retains_valid_initial_checkpoint(tmp_path):
    from society0.kernel.storage import StageStore
    closed=[]
    def install(context):
        context.on_close(lambda:closed.append(True))
        raise RuntimeError('install failed')
    plugin=Plugin('a',install=install,schema=('CREATE TABLE a(id INTEGER PRIMARY KEY)',))
    with pytest.raises(RuntimeError,match='install failed'):
        async with compose(tmp_path/'run',[plugin]): pass
    assert closed==[True]
    with StageStore.open(tmp_path/'run') as store:
        assert store.complete_step==0


@pytest.mark.asyncio
async def test_async_initializer_rejected_without_published_run(tmp_path):
    async def initialize(writer): pass
    with pytest.raises(TypeError,match='synchronous'):
        async with compose(tmp_path/'run',[Plugin('a',schema=('CREATE TABLE a(id INTEGER PRIMARY KEY)',),initialize=initialize)]): pass
    assert not (tmp_path/'run').exists()
