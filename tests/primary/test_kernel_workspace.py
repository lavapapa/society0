"""工作区增量的产品验收，使用真实共享 StageStore 和 Bashkit。"""
import pytest
from society0.kernel.actors import ActorRecord, actor_plugin
from society0.kernel.composition import compose
from society0.kernel.interaction import Actions, Information, InteractionScope, Moment
from society0.kernel.shell import ShellSession
from society0.kernel.workspace import workspace_plugin


def plugins():
    return [actor_plugin({'rule':lambda record:None}, records=[ActorRecord('alice', 'rule'), ActorRecord('bob', 'rule')]), workspace_plugin()]


def shell(host, tmp_path, actor='alice', time=1):
    scope=InteractionScope(actor, Moment(time, 'work'))
    return ShellSession(scope, Information(lambda *args: True), Actions(lambda *args: True),
                        result_dir=tmp_path/'output',workspace=host.service('workspace','workspace'))


@pytest.mark.asyncio
async def test_unchanged_workspace_does_not_prepare_cold_body_again(tmp_path):
    async with compose(tmp_path/'run',plugins()) as host:
        first=shell(host,tmp_path)
        result=await first.execute('cd /workspace; saved=kept; printf original > cold')
        assert result.exit_code==0
        first.save_workspace();await first.aclose()
        store=host.service('storage','store');store.complete(1)
        original=store.prepare_artifact;prepared=[]
        def record(chunks):
            def counted():
                for chunk in chunks:prepared.append(bytes(chunk));yield chunk
            return original(counted())
        store.prepare_artifact=record
        second=shell(host,tmp_path,time=2)
        result=await second.execute('cat /workspace/cold; printf "|%s" "$saved"')
        assert result.stdout=='original|kept'
        second.save_workspace();await second.aclose()
        assert all(b'original' not in chunk for chunk in prepared)


@pytest.mark.asyncio
async def test_file_delete_rename_restore_and_actor_isolation(tmp_path):
    async with compose(tmp_path/'run',plugins()) as host:
        first=shell(host,tmp_path)
        assert (await first.execute('mkdir -p /workspace/d; printf before > /workspace/d/a')).exit_code==0
        first.save_workspace();await first.aclose();host.service('storage','store').complete(1)
        next_shell=shell(host,tmp_path,time=2)
        assert (await next_shell.execute('mv /workspace/d/a /workspace/d/b; rm -r /workspace/d')).exit_code==0
        next_shell.save_workspace();await next_shell.aclose()
        bob=shell(host,tmp_path,actor='bob')
        assert (await bob.execute('cat /workspace/d/a')).exit_code!=0
        await bob.aclose()
        host.service('storage','store').abort_step()
    async with compose(tmp_path/'restored',plugins(),source=tmp_path/'run') as host:
        restored=shell(host,tmp_path,time=3)
        assert (await restored.execute('cat /workspace/d/a')).stdout=='before'
        assert (await restored.execute('cat /workspace/d/b')).exit_code!=0
        await restored.aclose()


@pytest.mark.asyncio
async def test_shared_world_mount_is_dynamic_readonly_and_authorized(tmp_path):
    from society0.kernel.information_sql import SQLInformation, DatasetSpec, DocumentSpec
    from society0.kernel.plugins import Plugin
    p=plugins()+[Plugin('docs',schema=(
        'CREATE TABLE docs(id INTEGER PRIMARY KEY,owner TEXT NOT NULL,body BLOB NOT NULL)',
        'CREATE INDEX docs_owner ON docs(owner,id)',
    ),initialize=lambda w:w.execute('INSERT INTO docs VALUES(1,?,?)',('alice','完整信息🙂'.encode())))]
    async with compose(tmp_path/'run',p) as host:
        store=host.service('storage','store');info=Information(lambda *a:True)
        access=lambda scope:('owner=?',(scope.actor,))
        info.mount('/docs',SQLInformation('docs',store,{
            'items':DatasetSpec('docs','id',('id','owner'),authorize=access),
            'texts':DocumentSpec('docs','id','body',authorize=access),
        }))
        scoped=InteractionScope('alice',Moment(1,'work'))
        one=ShellSession(scoped,info,Actions(lambda *a:True),result_dir=tmp_path/'out',workspace=host.service('workspace','workspace'))
        result=await one.execute('cat /world/docs/texts/1')
        assert result.stdout=='完整信息🙂' and result.exit_code==0
        assert json_load((await one.execute('cat /world/docs/items/1')).stdout)['owner']=='alice'
        assert (await one.execute('printf changed > /world/docs/texts/1')).exit_code!=0
        store.transaction(lambda w:w.execute('INSERT INTO docs VALUES(2,?,?)',('alice',b'next')))
        assert (await one.execute('ls /world/docs/texts')).stdout=='1\n2\n'
        scoped.close()
        with pytest.raises(Exception):await one.execute('cat /world/docs/texts/1')
        await one.aclose()
        other=ShellSession(InteractionScope('bob',Moment(1,'work')),info,Actions(lambda *a:True),result_dir=tmp_path/'bob',workspace=host.service('workspace','workspace'))
        assert (await other.execute('cat /world/docs/texts/1')).exit_code!=0
        await other.aclose()


def json_load(value):
    import json
    return json.loads(value)


@pytest.mark.asyncio
async def test_workspace_preserves_symlink_mode_time_and_temp_boundary(tmp_path):
    async with compose(tmp_path/'run',plugins()) as host:
        first=shell(host,tmp_path)
        result=await first.execute('printf body > target; ln -s target link; chmod 600 target; touch -t 202401020304.05 target; printf temporary > /tmp/transient; readlink link; stat -c "%a|%Y" target')
        assert result.exit_code==0,result.stderr
        original=result.stdout
        # 原生 Bashkit 的链接为 inert，保留 readlink 而不擅自跟随。
        assert (await first.execute('cat link')).exit_code!=0
        assert (await first.execute('mkfifo pipe')).exit_code!=0
        first.save_workspace();await first.aclose();host.service('storage','store').complete(1)
    async with compose(tmp_path/'restored',plugins(),source=tmp_path/'run') as host:
        resumed=shell(host,tmp_path,time=2)
        result=await resumed.execute('readlink link; stat -c "%a|%Y" target')
        assert result.stdout==original
        assert (await resumed.execute('cat target')).stdout=='body'
        assert (await resumed.execute('cat /tmp/transient')).exit_code!=0
        await resumed.aclose()


@pytest.mark.asyncio
async def test_private_directory_has_no_implicit_thousand_file_limit(tmp_path):
    async with compose(tmp_path/'run',plugins()) as host:
        service=host.service('workspace','workspace')
        lease=service.open(InteractionScope('alice',Moment(1,'work')))
        # 文件名为规范路径；百分号、下划线和中文不进入 LIKE 模式语义。
        lease.save(b'',{'removed':[],'entries':[
            {'path':f'/中_%/{i}','kind':'file','mode':420,'modified_ns':0,'created_ns':0,'content':b'x'} for i in range(1001)
        ]})
        assert len(await lease.callback('list','/中_%'))==1001
        lease.save(b'',{'removed':['/中_%'],'entries':[]})
        assert await lease.callback('list','/中_%')==[]


@pytest.mark.asyncio
async def test_world_callback_cancel_drains_and_storage_fault_propagates(tmp_path):
    import asyncio
    from society0.kernel.interaction import Ref,DocumentChunk,ResourceStat
    entered=asyncio.Event();cancelled=asyncio.Event()
    class Provider:
        def ref(self,path):return Ref('docs','document','1')
        def stat(self,scope,path):return ResourceStat('file',4,1,self.ref(path))
        async def read(self,scope,path,**options):
            entered.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
    info=Information(lambda *a:True);provider=Provider();info.mount('/docs',provider)
    session=ShellSession(InteractionScope('a',Moment(1,'work')),info,Actions(lambda *a:True),result_dir=tmp_path)
    task=asyncio.create_task(session.execute('cat /world/docs/1'))
    await entered.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert cancelled.is_set() and not session._world_files._tasks
    await session.aclose()
    def failed(*args,**kwargs):raise OSError('read disk failure')
    provider.read=failed
    session=ShellSession(InteractionScope('a',Moment(1,'work')),info,Actions(lambda *a:True),result_dir=tmp_path)
    with pytest.raises(OSError,match='read disk failure'):await session.execute('cat /world/docs/1')
    await session.aclose()
