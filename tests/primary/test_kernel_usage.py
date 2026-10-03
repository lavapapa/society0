"""资源事实和累计投影使用同一事务，查询不重放正文。"""
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
from society0.kernel.models import RESOURCE_SCHEMA, ResourceCalls
from society0.kernel.observation import Observation


def test_physical_retries_shared_batches_cache_and_restore(tmp_path):
    with StageStore.create(tmp_path/'run', THREAD_SCHEMA+RESOURCE_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',1,'decision')
        for identifier in ('failed','success'):
            threads.record_provider_request(tid,provider_options={'model':'chat'},physical_request_id=identifier)
        threads.record_provider_event(tid,'provider_error',{'physical_request_id':'failed','payload':{'message':'transport'}})
        threads.record_provider_event(tid,'provider_response',{'physical_request_id':'success','payload':{'raw_response':{'usage':{'prompt_tokens':10,'completion_tokens':3,'total_tokens':13}}}})
        calls=ResourceCalls(store)
        physical=calls.begin('embedding','endpoint','embed',{'texts':['x','y']})
        calls.event(physical,'response',{'response':{'usage':{'prompt_tokens':7,'total_tokens':7}}})
        for actor in ('a','b','a'):
            calls.begin('embedding_use','','embed',{'metadata':{'actor':actor},'sources':[{'call_id':physical,'item_index':0,'input_index':0}], 'status':'completed'})
        observer=Observation(store.path)
        result=observer.resource_usage()
        assert result['totals']['requests']==3
        assert result['totals']['responses']==2
        assert result['totals']['errors']==1
        assert result['totals']['input_tokens']==17
        assert result['totals']['input_reports']==2
        assert result['totals']['embedding_uses']==3
        a=observer.resource_usage(actor='a')
        assert a['attribution']=='related_physical_calls_not_additive'
        assert a['totals']['requests']==3 and a['totals']['embedding_uses']==2
        assert observer.resource_usage(actor='b')['totals']['requests']==1
        store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'fork',step=1) as store:
        assert Observation(store.path).resource_usage()['totals']==result['totals']


def test_usage_projection_rolls_back_with_original_fact(tmp_path,monkeypatch):
    import society0.kernel.threads as module
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        before=threads.describe(tid)['last_seq']
        original=module._append
        def fail(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError('write failed')
        monkeypatch.setattr(module,'_append',fail)
        import pytest
        with pytest.raises(RuntimeError):threads.record_provider_request(tid,provider_options={'model':'m'},physical_request_id='p')
        assert threads.describe(tid)['last_seq']==before
        assert Observation(store.path).resource_usage()['totals']['requests']==0


def test_usage_reads_fixed_projection_and_failed_step_stays_diagnostic(tmp_path):
    from society0.kernel import usage
    work=[]
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        def insert(writer,start,end):
            for index in range(start,end):usage.begin(writer,'thread',str(index),'m','a')
        for start,end in ((0,100),(100,10000)):
            store.transaction(lambda writer:insert(writer,start,end))
            count=[0]
            def read(view):
                view._connection.set_progress_handler(lambda:count.__setitem__(0,count[0]+1) or False,1)
                try:return usage.read(view,actor='a')
                finally:view._connection.set_progress_handler(None)
            assert store.read(read)['totals']['requests']==end
            work.append(count[0])
        print({"history_calls":[100,10000],"query_vm_instructions":work})
        assert work[1]<work[0]*2
        store.complete(1)
        store.transaction(lambda writer:usage.begin(writer,'thread','pending','m','a'))
        store.abort_step()
        assert Observation(store.path).resource_usage()['totals']['requests']==10001
    with StageStore.restore(tmp_path/'run',tmp_path/'restored',step=1) as restored:
        result=Observation(restored.path).resource_usage()
        assert result['totals']['requests']==10000
        assert result['totals']['unknown_usage_calls']==10000


import pytest
@pytest.mark.asyncio
async def test_real_adapters_project_retry_response_and_shared_cache(tmp_path):
    import asyncio,httpx,openai
    from openai.types.chat import ChatCompletion
    from openai.types import CreateEmbeddingResponse
    from society0.kernel.models import ModelProvider,EmbeddingProvider
    config={'id':'endpoint','model':'chat','api_key':'unused','base_url':'http://unused.invalid/v1','trust_env':False,'concurrency':2}
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA+RESOURCE_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        threads.append_message(tid,{'role':'user','content':'full input'})
        model=ModelProvider([config],threads,max_attempts=2,retry_delay=0)
        attempted=[]
        async def create(**kwargs):
            attempted.append(kwargs)
            if len(attempted)==1:raise openai.APIConnectionError(request=httpx.Request('POST','http://unused.invalid'))
            return ChatCompletion(id='r',created=0,model='chat',object='chat.completion',choices=[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'done'}}],usage={'prompt_tokens':11,'completion_tokens':2,'total_tokens':13})
        model.manager.clients['endpoint'].chat.completions.create=create
        try:await model.request(tid,{})
        finally:await model.close()
        embedding=EmbeddingProvider([{**config,'model':'embed','dimensions':2}],store,threads,dimensions=2,batch_wait_ms=10)
        batches=[]
        async def embed(**kwargs):
            batches.append(kwargs)
            return CreateEmbeddingResponse(model='embed',object='list',usage={'prompt_tokens':4,'total_tokens':4},data=[{'index':i,'object':'embedding','embedding':[1.,2.]} for i in range(len(kwargs['input']))])
        embedding.manager.clients['endpoint'].embeddings.create=embed
        try:
            await asyncio.gather(embedding.embed(['first'],metadata={'actor':'a','thread_id':tid}),embedding.embed(['second'],metadata={'actor':'b'}))
            await embedding.embed(['first'],metadata={'actor':'c'})
        finally:await embedding.close()
        result=Observation(store.path).resource_usage()
        assert len(batches)==1
        assert result['totals']['requests']==3
        assert result['totals']['errors']==1 and result['totals']['responses']==2
        assert result['totals']['total_tokens']==17
        assert result['totals']['unknown_usage_calls']==1
        assert Observation(store.path).resource_usage(actor='c')['totals']['total_tokens']==4
        assert Observation(store.path).resource_usage(actor='c')['totals']['embedding_uses']==1


def test_generic_response_event_does_not_claim_a_physical_call(tmp_path):
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        response={'content':'rule/fake diagnostic'}
        threads.event(tid,'provider_response',response)
        assert threads.tail(tid)['items'][-1]['payload']==response
        assert Observation(store.path).resource_usage()['totals']['requests']==0


def test_response_decode_failure_and_cancellation_are_distinct_facts(tmp_path):
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        for identifier in ('decode','cancel','after_response'):
            threads.record_provider_request(tid,provider_options={'model':'m'},physical_request_id=identifier)
        body={'usage':{'prompt_tokens':5,'total_tokens':5}}
        threads.record_provider_event(tid,'provider_decode_error',{'physical_request_id':'decode','payload':{'response':body}})
        threads.record_provider_event(tid,'provider_cancelled',{'physical_request_id':'cancel','payload':{}})
        threads.record_provider_event(tid,'provider_response',{'physical_request_id':'after_response','payload':{'raw_response':body}})
        threads.record_provider_event(tid,'provider_decode_error',{'physical_request_id':'after_response','payload':{'response':body}})
        counts=Observation(store.path).resource_usage()['totals']
        assert counts['requests']==3 and counts['responses']==2
        assert counts['errors']==2 and counts['decode_errors']==2 and counts['cancelled']==1
        assert counts['input_tokens']==10 and counts['input_reports']==2


def test_physical_response_requires_its_request_thread(tmp_path):
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);a=threads.open('a',0,'decision');b=threads.open('b',0,'decision')
        threads.record_provider_request(a,provider_options={'model':'m'},physical_request_id='p')
        before=threads.describe(b)['last_seq']
        with pytest.raises(KeyError):
            threads.record_provider_event(b,'provider_response',{'physical_request_id':'p','payload':{}})
        assert threads.describe(b)['last_seq']==before


def test_projection_failure_rolls_back_full_request(tmp_path,monkeypatch):
    from society0.kernel import usage
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        before=threads.describe(tid)['last_seq'];original=usage.begin
        def fail(*args,**kwargs):
            original(*args,**kwargs)
            raise OSError('projection write failed')
        monkeypatch.setattr(usage,'begin',fail)
        with pytest.raises(OSError):threads.record_provider_request(tid,provider_options={'model':'m'},physical_request_id='p')
        assert threads.describe(tid)['last_seq']==before
        assert Observation(store.path).resource_usage()['totals']['requests']==0


def test_non_model_resource_event_keeps_generic_fact_contract(tmp_path):
    with StageStore.create(tmp_path/'run',RESOURCE_SCHEMA) as store:
        calls=ResourceCalls(store);identifier=calls.begin('export','','',{})
        calls.event(identifier,'response',{'rows':3})
        assert calls.read(identifier)[-1]['payload']=={'rows':3}
        assert Observation(store.path).resource_usage()['totals']['requests']==0


def test_http_usage_and_prepared_complete_share_same_projection(tmp_path):
    import json,threading,http.client,time
    from society0.kernel.observation import ObservationService,make_server
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        threads.record_provider_request(tid,provider_options={'model':'m'},physical_request_id='complete')
        store.complete(1)
        threads.record_provider_request(tid,provider_options={'model':'m'},physical_request_id='pending')
        with ObservationService(store.path) as service:
            server=make_server(service,port=0);runner=threading.Thread(target=server.serve_forever);runner.start()
            try:
                connection=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=3)
                connection.request('POST','/',json.dumps({'method':'resource_usage','params':{'actor':'a'}}))
                response=connection.getresponse();live=json.loads(response.read());connection.close()
                assert response.status==200 and live['totals']['requests']==2 and live['complete']['step']==1
                service.prepare_complete(1);deadline=time.monotonic()+10
                while True:
                    state=service.preparation_status()
                    if state['state']=='ready':break
                    assert time.monotonic()<deadline;time.sleep(.01)
                selected=service.call('resource_usage',{'actor':'a','view':state['view']})
                assert selected['totals']['requests']==1
            finally:server.shutdown();server.server_close();runner.join()
