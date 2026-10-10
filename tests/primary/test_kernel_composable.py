"""静态配方、数据依赖和资源所有权的失败先行合同。"""
from contextlib import contextmanager
import pytest
from society0.kernel.plugins import Plugin, PluginHost
from society0.kernel.composition import compose
from society0.kernel.interaction import Information
from society0.kernel import actors


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['duplicate', 'missing_service', 'missing_schema', 'service_cycle', 'schema_cycle'])
async def test_preflight_before_prepare_and_directory(tmp_path, case):
    calls=[]
    @contextmanager
    def prepare():
        calls.append('prepare')
        yield lambda w:None
    child=Plugin('child',prepare=prepare)
    if case=='duplicate': roots=[Plugin('root',includes=(child,)),child]
    elif case=='missing_service': roots=[Plugin('root',includes=(child,),requires=('absent',))]
    elif case=='missing_schema': roots=[Plugin('root',includes=(child,),schema_requires=('absent',))]
    elif case=='service_cycle': roots=[Plugin('root',includes=(child,),requires=('other',)),Plugin('other',requires=('root',))]
    else: roots=[Plugin('root',includes=(child,),schema_requires=('other',)),Plugin('other',schema_requires=('root',))]
    with pytest.raises(ValueError):
        async with compose(tmp_path/'run',roots): pass
    assert calls==[]
    assert not (tmp_path/'run').exists()
    with pytest.raises(ValueError):
        async with PluginHost(roots): pass


@pytest.mark.asyncio
async def test_independent_graphs_restore_and_nested_schema(tmp_path):
    calls=[]
    @contextmanager
    def prepare():
        calls.append('prepare')
        yield lambda w:w.execute('INSERT INTO a VALUES(7)')
    def install_a(ctx):
        assert ctx.require('b','value')==8
        calls.append('install a')
    def install_b(ctx):
        ctx.provide('value',ctx.require('storage','store').read(lambda r:r.query('SELECT value FROM b')[0][0]))
        calls.append('install b')
    a=Plugin('a',requires=('b',),install=install_a,schema=('CREATE TABLE a(value INTEGER PRIMARY KEY)',),prepare=prepare)
    b=Plugin('b',requires=('storage',),schema_requires=('a',),install=install_b,
             schema=('CREATE TABLE b(value INTEGER PRIMARY KEY)',),initialize=lambda w:w.execute('INSERT INTO b SELECT value+1 FROM a'))
    recipe=Plugin('recipe',includes=(Plugin('nested',includes=(b,a)),))
    async with compose(tmp_path/'source',[recipe]): pass
    assert calls==['prepare','install b','install a']
    async with compose(tmp_path/'restored',[Plugin('other_recipe',includes=(a,b))],source=tmp_path/'source'): pass
    assert calls==['prepare','install b','install a','install b','install a']


@pytest.mark.asyncio
async def test_actor_directory_breaks_driver_environment_cycle_and_restores(tmp_path):
    def environment(ctx):
        directory=ctx.require('actors.data','directory')
        assert directory.view('a').persona=='full persona'
        directory.set_state('a','environment',True)
        ctx.provide('directory',directory)
    def driver(ctx):
        directory=ctx.require('environment','directory')
        ctx.provide('factory',lambda record:('driver',directory.view(record.id).state))
    plugins=[actors.actor_plugin({'rule':('driver','factory')},records=[actors.ActorRecord('a','rule','full persona')]),
             Plugin('environment',requires=('actors.data',),install=environment),
             Plugin('driver',requires=('environment',),install=driver)]
    for name,source in [('source',None),('restored',tmp_path/'source')]:
        async with compose(tmp_path/name,plugins,source=source) as host:
            runtime=host.service('actors','actors')
            assert runtime['a'].driver==('driver',{'environment':True})
            assert isinstance(host.service('actors.data','directory'),actors.ActorDirectory)
    async with compose(tmp_path/'data_only',[actors.actor_data_plugin(records=[actors.ActorRecord('b','not-installed')])]) as host:
        assert host.service('actors.data','directory').get_record('b').driver=='not-installed'


@pytest.mark.asyncio
async def test_information_borrowed_and_owned_cleanup_once_in_reverse_despite_failure():
    calls=[]
    class Provider:
        def __init__(self,name,fail=False): self.name,self.fail=name,fail
        async def close(self):
            calls.append(self.name)
            if self.fail: raise RuntimeError('close failed')
    info=Information(lambda *args:True)
    first,second,borrowed=Provider('first'),Provider('second',True),Provider('borrowed')
    info.mount('/a',first,owned=True)
    info.mount('/b',second,owned=True)
    info.mount('/again',first,owned=True)
    info.mount('/borrowed',borrowed)
    with pytest.raises(RuntimeError,match='close failed'): await info.close()
    assert calls==['second','first']
    await info.close()
    assert calls==['second','first']


@pytest.mark.asyncio
async def test_mechanism_owner_closes_before_dependencies_even_on_install_failure():
    calls=[]
    def dependency(ctx):
        info=Information(lambda *args:True)
        ctx.provide('information',info)
        ctx.on_close(lambda:calls.append('dependency'))
        ctx.on_close(info.close)
    def mechanism(ctx):
        class Provider:
            def close(self): calls.append('mechanism')
        provider=Provider()
        ctx.on_close(provider.close)
        info=ctx.require('dependency','information')
        info.mount('/a',provider)
        info.mount('/b',provider)
        raise RuntimeError('install failed')
    with pytest.raises(RuntimeError,match='install failed'):
        async with PluginHost([Plugin('mechanism',requires=('dependency',),install=mechanism),Plugin('dependency',install=dependency)]): pass
    assert calls==['mechanism','dependency']


@pytest.mark.asyncio
async def test_explicit_actor_data_shared_by_runtime_and_rejects_invalid_binding(tmp_path):
    data=actors.actor_data_plugin(name='directory',records=[actors.ActorRecord('a','rule')])
    runtime=actors.actor_plugin({'rule':lambda record:record.id},name='runtime',data=('directory','directory'))
    async with compose(tmp_path/'run',[runtime,data]) as host:
        assert host.service('runtime','actors')['a'].driver=='a'
        host.service('directory','directory').add(actors.ActorRecord('b','unknown'))
        with pytest.raises(KeyError,match='unknown'): host.service('runtime','actors')['b']
        with pytest.raises(KeyError,match='unknown'): host.service('runtime','actors').add(actors.ActorRecord('c','unknown'))
        host.service('storage','store').complete(1)
    with pytest.raises(ValueError,match='unresolved actor drivers: unknown'):
        async with compose(tmp_path/'restore',[runtime,data],source=tmp_path/'run'): pass
    with pytest.raises(ValueError,match='records belong'):
        actors.actor_plugin({},data=('directory','directory'),records=[actors.ActorRecord('c','unknown')])


@pytest.mark.asyncio
async def test_schema_dependency_does_not_grant_service_access():
    def consumer(ctx): ctx.require('data','value')
    with pytest.raises(ValueError,match='undeclared plugin dependency'):
        async with PluginHost([Plugin('data',install=lambda ctx:ctx.provide('value',1)),
                               Plugin('consumer',schema_requires=('data',),install=consumer)]): pass


def test_static_plugin_sequences_are_frozen():
    children=[Plugin('child')]
    schema_requires=['child']
    root=Plugin('root',includes=children,schema_requires=schema_requires)
    children.clear();schema_requires.clear()
    assert tuple(child.name for child in root.includes)==('child',)
    assert root.schema_requires==('child',)


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['list','list_files','read','query','stat','search_revision','metadata'])
async def test_router_scope_dependencies_are_per_call_and_keep_original_lifetime(operation):
    import asyncio
    from society0.kernel.interaction import InteractionScope, Moment, Query, Ref, ResourceStat, ScopeClosed
    entered,release=asyncio.Event(),asyncio.Event()
    seen=[]
    class Provider:
        def ref(self,path): return Ref('data','dataset','')
        async def call(self,scope,*args,**kwargs):
            seen.append(scope)
            entered.set()
            await release.wait()
            scope.check_active()
            return ResourceStat('directory',None,1,self.ref(''))
        list=list_files=read=query=stat=search_revision=metadata=call
    provider=Provider()
    one=Information(lambda *args:True,access_dependencies=('acl_a',))
    two=Information(lambda *args:True,access_dependencies=('acl_b',))
    one.mount('/data',provider);two.mount('/data',provider)
    original=InteractionScope('a',Moment(1,'read'))
    other=InteractionScope('b',Moment(1,'read'))
    args=('/data',Query()) if operation=='query' else ('/data',)
    async def invoke(info,scope):
        try:return await getattr(info.bound(scope),operation)(*args)
        except Exception as error:return error
    async with asyncio.TaskGroup() as group:
        first=group.create_task(invoke(one,original))
        await entered.wait();entered.clear()
        second=group.create_task(invoke(two,other))
        await entered.wait()
        assert seen[0].access_dependencies==('acl_a',)
        assert seen[1].access_dependencies==('acl_b',)
        assert original.access_dependencies==other.access_dependencies==()
        error=RuntimeError('provider failed')
        seen[0].fail(error)
        with pytest.raises(RuntimeError,match='provider failed'):original.check_active()
        other.close()
        release.set()
    assert first.result() is error
    assert isinstance(second.result(),ScopeClosed)
    assert not hasattr(provider,'access_dependencies')
    await one.close();await two.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation,path', [
    ('list','/data'),('list','/data/rows'),('list_files','/data'),('list_files','/data/rows'),
    ('read','/data/rows/1'),('read','/data/doc/1'),('stat','/data'),('stat','/data/doc/1'),
    ('search_revision','/data'),('metadata','/data/rows'),('query','/data/rows'),
])
async def test_sql_router_and_provider_dependency_revisions_all_paths(tmp_path,operation,path):
    from society0.kernel.information_sql import SQLInformation, DatasetSpec, DocumentSpec
    from society0.kernel.interaction import InteractionScope, Moment, Query
    schema=('CREATE TABLE rows(id INTEGER PRIMARY KEY,body TEXT NOT NULL)',
            *(f'CREATE TABLE {table}(id INTEGER PRIMARY KEY,value INTEGER)' for table in ('acl_a','acl_b','provider_acl')))
    def initialize(w):
        w.execute("INSERT INTO rows VALUES(1,'full body'),(2,'second body')")
        for table in ('acl_a','acl_b','provider_acl'):w.execute(f'INSERT INTO {table} VALUES(1,0)')
    async with compose(tmp_path/'run',[Plugin('data',schema=schema,initialize=initialize)]) as host:
        store=host.service('storage','store')
        provider=SQLInformation('data',store,{'rows':DatasetSpec('rows','id',('id','body')),
                                             'doc':DocumentSpec('rows','id','body')},access_dependencies=('provider_acl',))
        one=Information(lambda *args:True,access_dependencies=('acl_a',))
        two=Information(lambda *args:True,access_dependencies=('acl_b',))
        one.mount('/data',provider);two.mount('/data',provider)
        scope=InteractionScope('a',Moment(1,'read'))
        async def version():
            args=(path,Query(limit=1)) if operation=='query' else (path,)
            result=await getattr(one.bound(scope),operation)(*args)
            if operation=='search_revision':return result
            return result['revision'] if operation=='metadata' else result.revision
        try:
            before=await version()
            store.transaction(lambda w:w.execute('UPDATE acl_b SET value=1'))
            assert await version()==before
            store.transaction(lambda w:w.execute('UPDATE acl_a SET value=1'))
            after=await version()
            assert after!=before
            store.transaction(lambda w:w.execute('UPDATE provider_acl SET value=1'))
            assert await version()!=after
            assert provider.access_dependencies==('provider_acl',)
            source=provider._record_source
            await one.close();await two.close()
            if source is not None:assert not source[1].closed
        finally:
            await one.close();await two.close();provider.close()
        if source is not None:assert source[1].closed
