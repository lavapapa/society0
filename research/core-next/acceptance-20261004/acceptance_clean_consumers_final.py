"""从隔离wheel执行base及各extra的实际公开消费者。"""
import asyncio,json,sys,tempfile,importlib.util
from pathlib import Path
from society0.kernel.storage import StageStore
from society0.kernel.composition import compose
from society0.kernel.plugins import Plugin,PluginHost
from society0.kernel.services import thread_plugin
async def main(name):
 with tempfile.TemporaryDirectory(prefix='society0-clean-consumer-') as directory:
  path=Path(directory)
  if name=='base':
   from society0.kernel.runner import RunPlan,RunContract,run_plan
   from society0.kernel.schedule import FixedStep,schedule_plugin
   from society0.kernel.runtime import runtime_plugin
   from society0.kernel.results import results_plugin
   from society0.kernel.interaction import interaction_plugin
   services={}
   def install(ctx):services['store']=ctx.require('storage','store')
   def phase(ctx):services['store'].transaction(lambda w:w.execute('UPDATE counter SET n=n+1'))
   plan=RunPlan(plugins=[Plugin('counter',('storage',),install,schema=('CREATE TABLE counter(id INTEGER PRIMARY KEY NOT NULL,n INTEGER NOT NULL)',),initialize=lambda w:w.execute('INSERT INTO counter VALUES(1,0)')),interaction_plugin(lambda *args:True),results_plugin(),runtime_plugin(information=('interaction','information'),actions=('interaction','actions'),store=('storage','store'),results=('results','results')),schedule_plugin(FixedStep(phase))],moments=iter((1,2)),contract=RunContract(release={'commit':'clean-install'},dependencies={},configuration={},time={},budgets={}))
   result=await run_plan(path/'run',plan)
   with StageStore.restore(path/'run',path/'restored') as restored:assert restored.complete_step==2 and restored.read(lambda v:v.query('SELECT n FROM counter'))==[(2,)]
   assert result['status']=='completed' and importlib.util.find_spec('pydantic_ai') is None
   from society0.kernel.datasets import Datasets,DATASET_SCHEMA
   assert importlib.util.find_spec('pyzstd') is None
   with StageStore.create(path/'dataset',DATASET_SCHEMA) as store:
    reference=Datasets(store).import_rows('original',[{'text':'完整🙂'*40000},None]);store.complete(1)
   with StageStore.restore(path/'dataset',path/'dataset-restored') as restored:
    assert Datasets(restored).get(reference,0)=={'text':'完整🙂'*40000}
    assert Datasets(restored).get(reference,1) is None
  elif name in ('llm','anthropic','google'):
   from society0.kernel.models import model_plugin,embedding_plugin
   endpoint={'id':'offline','model':{'llm':'fixture','anthropic':'claude-sonnet-4-6','google':'gemini-2.5-flash'}[name],'api_key':'unused','provider_type':'openai' if name=='llm' else name,'base_url':'http://unused.invalid/v1','trust_env':False,'concurrency':1}
   plugins=[thread_plugin(),model_plugin({'main':{'endpoints':[endpoint]}})]
   if name!='anthropic':plugins.append(embedding_plugin({'main':{'endpoints':[dict(endpoint,model='embedding-fixture')]}}))
   async with compose(path/'run',plugins) as host:
    providers=[host.service('models','models')['main']]
    if name!='anthropic':providers.append(host.service('embeddings','embeddings')['main'])
    for provider in providers:await provider._start()
   assert all(provider._closed for provider in providers)
   if name=='llm':assert importlib.util.find_spec('httpx') is None
  elif name=='datasets':
   from society0.kernel.datasets import Datasets,DATASET_SCHEMA
   with StageStore.create(path/'run',DATASET_SCHEMA) as store:
    reference=Datasets(store).import_rows('原文',[{'body':'完整中文🙂'},{'body':'次条'}]);store.complete(1)
   with StageStore.restore(path/'run',path/'restored') as store:assert Datasets(store).get(reference,0)=={'body':'完整中文🙂'}
  elif name=='observe':
   from society0.kernel.observation import Observation,make_app
   with StageStore.create(path/'run',()):pass
   service=Observation(path/'run');assert service.status()['complete']['step']==0;make_app(service);service.close()
  elif name=='shell':
   from society0.kernel.shell import ShellSession
   from society0.kernel.interaction import InteractionScope,Moment,Information,Actions
   session=ShellSession(InteractionScope('a',Moment(0,'work')),Information(lambda *a:True),Actions(lambda *a:True),result_dir=path/'output')
   assert (await session.execute('printf original | cat')).stdout=='original';await session.aclose()
  elif name=='memory':
   import chromadb
   from society0.kernel.memory import MemoryPolicy
   from society0.kernel.services import memory_plugin
   from society0.kernel.interaction import interaction_plugin
   class Embed:
    async def embed(self,texts,*,metadata):return [[1.,0.] for _ in texts]
   def install(ctx):
    client=chromadb.PersistentClient(path=str(path/'vectors'));ctx.on_close(client.close);ctx.provide('client',client);ctx.provide('embeddings',{'fixed':Embed()})
   async with compose(path/'run',[thread_plugin(),interaction_plugin(lambda *a:True),Plugin('resource',install=install),memory_plugin(client=('resource','client'),embedding=('resource','embeddings','fixed'),policy=MemoryPolicy(False,False,False))]) as host:
    memory=host.service('memory','memory');threads=host.service('threads','threads');tid=threads.open('a',0,'decision')
    await memory.finish_job(memory.prepare_job('a',tid,'job',timestamp=0,entries=[{'content':'完整原文'}]))
    assert [m['content'] for m in await memory.recall('a','原文',current_step=1)]==['完整原文']
  elif name=='social':
   from society0.plugins.social_topology import generate_topology
   from society0.plugins.social_models import SocialNetworkConfig
   network=generate_topology(['a','b'],SocialNetworkConfig(distribution={'type':'complete'}));assert network.number_of_nodes()==2 and network.number_of_edges()==2
  else:raise ValueError(name)
  import society0
  print(json.dumps({'profile':name,'public_consumer':True,'wheel_import':society0.__file__},ensure_ascii=False))
asyncio.run(main(sys.argv[1]))
