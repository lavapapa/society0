"""持久工作区跨激活与失败原子域的非作者消费者。"""
import pytest
from society0.kernel.actors import ActorRecord,actor_plugin
from society0.kernel.composition import compose
from society0.kernel.interaction import InteractionScope,Moment,Information,Actions
from society0.kernel.shell import ShellSession
from society0.kernel.workspace import workspace_plugin


def plugins():return [actor_plugin({'rule':lambda record:None},records=[ActorRecord('a','rule')]),workspace_plugin()]

def shell(host,path):
    return ShellSession(InteractionScope('a',Moment(1,'work')),Information(lambda *a:True),Actions(lambda *a:True),
                        result_dir=path/'output',workspace=host.service('workspace','workspace'))


@pytest.mark.asyncio
async def test_review_directory_replace_recreate_and_old_complete(tmp_path):
    async with compose(tmp_path/'run',plugins()) as host:
        one=shell(host,tmp_path)
        assert (await one.execute('mkdir -p /workspace/a/n; printf old > /workspace/a/n/x; printf target > /workspace/target')).exit_code==0
        one.save_workspace();await one.aclose();host.service('storage','store').complete(1)
        two=shell(host,tmp_path)
        assert (await two.execute('mv /workspace/a/n/x /workspace/target; mv /workspace/a /workspace/b; rm -r /workspace/b; mkdir /workspace/b; printf new > /workspace/b/y')).exit_code==0
        two.save_workspace();await two.aclose();host.service('storage','store').complete(2)
        three=shell(host,tmp_path)
        assert (await three.execute('cat /workspace/target /workspace/b/y')).stdout=='oldnew'
        assert (await three.execute('test -e /workspace/b/n/x')).exit_code!=0
        await three.aclose()
    async with compose(tmp_path/'old',plugins(),source=tmp_path/'run',step=1) as host:
        restored=shell(host,tmp_path)
        assert (await restored.execute('cat /workspace/a/n/x /workspace/target')).stdout=='oldtarget'
        assert (await restored.execute('test -e /workspace/b')).exit_code!=0
        await restored.aclose()


@pytest.mark.asyncio
async def test_review_failed_sql_apply_leaves_no_partial_file_or_artifact_dependency(tmp_path):
    async with compose(tmp_path/'run',plugins()) as host:
        store=host.service('storage','store')
        lease=host.service('workspace','workspace').open(InteractionScope('a',Moment(1,'work')))
        def entry(path,content):return {'path':path,'kind':'file','content':content,'mode':420,'created_ns':0,'modified_ns':0}
        lease.save(b'original',{'removed':[],'entries':[entry('/kept',b'old')]});store.complete(1)
        malformed=entry('/second',b'new');del malformed['mode']
        with pytest.raises(KeyError):lease.save(b'changed',{'removed':['/kept'],'entries':[entry('/new',b'first'),malformed]})
        assert await lease.callback('read','/kept')==b'old'
        assert not await lease.callback('exists','/new')
        descriptor=store.complete(2)
        assert descriptor['artifacts']==[]
        await lease.close()
