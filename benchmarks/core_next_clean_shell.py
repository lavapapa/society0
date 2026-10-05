import asyncio,tempfile,importlib.util
from pathlib import Path
from society0.kernel.actors import ActorRecord,actor_plugin
from society0.kernel.workspace import workspace_plugin
from society0.kernel.composition import compose
from society0.kernel.interaction import InteractionScope,Moment,Information,Actions
from society0.kernel.shell import ShellSession
for name in ('openai','chromadb','networkx','ollama'):
    assert importlib.util.find_spec(name) is None,name
async def main():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d)
        plugins=[actor_plugin({'rule':lambda record:None},records=[ActorRecord('a','rule')]),workspace_plugin()]
        for step in (1,2):
            options={} if step==1 else {'source':root/'run'}
            async with compose(root/('run' if step==1 else 'restored'),plugins,**options) as host:
                shell=ShellSession(InteractionScope('a',Moment(step,'work')),Information(lambda *a:True),Actions(lambda *a:True),result_dir=root/'out',workspace=host.service('workspace','workspace'))
                cmd='printf 完整信息 > note; cat note' if step==1 else 'cat note'
                assert (await shell.execute(cmd)).stdout=='完整信息'
                shell.save_workspace();await shell.aclose()
                if step==1:host.service('storage','store').complete(1)
asyncio.run(main())
print('clean shell wheel: private file complete/restore bytes passed; no model/vector/social dependencies')
