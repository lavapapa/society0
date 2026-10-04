"""真实 LLMDriver、Bashkit、ActorStore 与完整恢复组合。"""
from tests.primary.scripted_provider import TypedScriptProvider
import json
import pytest
from society0.kernel.actors import ActorRecord, actor_plugin
from society0.kernel.composition import compose
from society0.kernel.plugins import Plugin
from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
from society0.kernel.interaction import Actions, Information
from society0.kernel.runtime import Runtime, Phase
from society0.kernel.llm import LLMDriver
from society0.kernel.shell import ShellSession
from society0.kernel.workspace import workspace_plugin


class Provider:
    def __init__(self, script):
        self.responses=iter([
            {'role':'assistant','content':'','finish_reason':'tool_calls','tool_calls':[
                {'id':'shell1','type':'function','function':{'name':'bash','arguments':json.dumps({'script':script})}}]},
            {'role':'assistant','content':'done','finish_reason':'stop'},
        ])
    async def request(self, thread_id, options): return next(self.responses)


@pytest.mark.asyncio
async def test_llm_workspace_survives_complete_restore_and_next_moment(tmp_path):
    holder={}
    creations=[]
    def factory(record):
        creations.append(record.id)
        return holder['driver']
    plugins=[actor_plugin({'llm':factory},records=[ActorRecord('alice','llm'),ActorRecord('idle','llm')]),
             Plugin('threads',schema=THREAD_SCHEMA),workspace_plugin()]
    async def run(host, step, moment, script, result_dir):
        actors=host.service('actors','actors')
        store=host.service('storage','store')
        threads=ThreadStore(store)
        info=Information(lambda *a:True)
        actions=Actions(lambda *a:True)
        def shell(session, ledger):
            return ShellSession(session.scope,info,bound_actions=ledger,result_dir=result_dir,
                                workspace=host.service('workspace','workspace'))
        holder['driver']=LLMDriver(TypedScriptProvider(Provider(script), threads),threads,input_builder=lambda s:[],shell_factory=shell)
        runtime=Runtime(actors,information=info,actions=actions,store=store)
        async def activate(ctx):
            ctx.activate('alice')
            results=await ctx.drain()
            assert results[0].result.status=='completed'
        await runtime.run_step(step,moment,[Phase('work',activate)])
        tid=threads.find('alice',{'time':moment,'phase':'work'})
        receipt=threads.get_tool_result(tid,'shell1')
        return json.loads(receipt['content']),store.read(lambda r:r.query('SELECT state FROM workspace_heads WHERE actor=?',('alice',))[0][0])
    async with compose(tmp_path/'run',plugins) as host:
        first,workspace=await run(host,1,1,'mkdir -p /workspace; cd /workspace; saved=kept; printf "主体原文🙂" > note',tmp_path/'outputs1')
        assert workspace and creations==['alice']
    async with compose(tmp_path/'restored',plugins,source=tmp_path/'run') as host:
        second,workspace=await run(host,2,2,'cat note; printf "|%s" "$saved"; pwd',tmp_path/'outputs2')
        assert second['stdout']=='主体原文🙂|kept/workspace\n'
        assert workspace and creations==['alice','alice']
        assert host.service('storage','store').complete_step==2
