"""显式调用的少量真实 profile 探针；凭据只在进程内读取。"""
import argparse
import asyncio
import json
from pathlib import Path
import re
import shlex
import time

import httpx
from society0.kernel.models import ModelProvider, EmbeddingProvider, ResourceCalls, RESOURCE_SCHEMA
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA, ThreadStore


async def main(args):
    if args.env_file:
        entries={}
        for line in Path(args.env_file).read_text().splitlines():
            pair=line.removeprefix('export ').partition('=')
            if pair[1] and pair[0].strip() in ('INDUSTRY_CHAIN_PROVIDER_AB_LLM_API_KEY','INDUSTRY_CHAIN_PROVIDER_AB_EMBED_API_KEY'):
                entries[pair[0].strip()]=shlex.split(pair[2])[0]
        key=entries['INDUSTRY_CHAIN_PROVIDER_AB_LLM_API_KEY']
        embed_key=entries['INDUSTRY_CHAIN_PROVIDER_AB_EMBED_API_KEY']
    else:
        section=Path(args.key_file).read_text().split('OpenRouter',1)[1].split('\n\n\n',1)[0]
        key=re.findall(r'sk-or-v1-[A-Za-z0-9_-]+',section)[0]
        embed_key=key
    destination=Path(args.output)
    destination.mkdir(parents=True,exist_ok=True)
    endpoint=args.base_url
    summary={'endpoint':endpoint,'model':args.embed_model if args.embedding_adapter_only else args.llm_model,'samples':[]}
    if args.embedding_adapter_only:
        with StageStore.create(destination/'run',[*THREAD_SCHEMA,*RESOURCE_SCHEMA]) as store:
            threads=ThreadStore(store)
            tids=[threads.open(actor,0,'embedding_probe') for actor in ('a','b')]
            provider=EmbeddingProvider([{'id':'embedding','api_key':embed_key,'base_url':endpoint,'model':args.embed_model,
                'concurrency':1,'timeout':30,'trust_env':False,'send_dimensions':not args.omit_dimensions}],store,threads,
                dimensions=args.embed_dimensions,max_attempts=1)
            start=time.perf_counter()
            try:
                vectors=await asyncio.gather(*[provider.embed([text],metadata={'actor':actor,'thread_id':tid})
                    for actor,tid,text in zip(('a','b'),tids,('主体甲保留原文。','主体乙独立原文。'))])
                physical=store.read(lambda r:r.query("SELECT id FROM resource_calls WHERE kind='embedding'"))
                assert len(physical)==1
                identifier=physical[0][0]
                before=provider.calls.read(identifier)
                assert before[0]['payload']['request']['input']==['主体甲保留原文。','主体乙独立原文。']
                store.complete(1)
                summary['samples'].append({'kind':'embedding_adapter','elapsed_s':time.perf_counter()-start,
                    'vectors':[len(group[0]) for group in vectors],'physical_calls':len(physical),'call_id':identifier})
            finally:await provider.close()
        with StageStore.restore(destination/'run',destination/'restored',step=1) as restored:
            assert ResourceCalls(restored).read(identifier)==before
            restored_threads=ThreadStore(restored)
            assert all(any(row['kind']=='resource_call_ref' for row in restored_threads.tail(tid)['items']) for tid in tids)
        summary['samples'][0]['restore_exact']=True
        (destination/'result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
        print(json.dumps(summary,ensure_ascii=False))
        return
    schema={'type':'object','properties':{'status':{'type':'string','enum':['ok']}},'required':['status'],'additionalProperties':False}
    base={'max_tokens':256,'temperature':0,'parallel_tool_calls':False,
          'extra_body':{'enable_thinking':False} if args.env_file else {'reasoning':{'enabled':False},'provider':{'allow_fallbacks':False}}}
    with StageStore.create(destination/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store)
        provider=ModelProvider([{'id':'openrouter','api_key':key,'base_url':endpoint,'model':summary['model'],
                                'concurrency':1,'timeout':60,'trust_env':False}],threads,max_attempts=1)
        try:
            for name,prompt,options in (
                ('schema','Return the JSON object with status ok.',{'response_format':{'type':'json_schema','json_schema':{'name':'probe','strict':True,'schema':schema}}}),
                ('tool','Call acknowledge once with status ok. Do not respond with prose.',{'tools':[{'type':'function','function':{'name':'acknowledge','description':'Record a harmless probe acknowledgement.','parameters':schema,'strict':True}}],'tool_choice':'auto'})):
                tid=threads.open('probe',name,'profile')
                threads.append_message(tid,{'role':'user','content':prompt})
                start=time.perf_counter()
                try:
                    result=await provider.request(tid,{**base,**options})
                    summary['samples'].append({'kind':name,'elapsed_s':time.perf_counter()-start,'thread_id':tid,'result':result})
                    threads.close(tid,'completed')
                except Exception as error:
                    summary['samples'].append({'kind':name,'elapsed_s':time.perf_counter()-start,'thread_id':tid,'error_type':type(error).__name__,'error':str(error).replace(key,'[credential]')})
                    threads.close(tid,'incomplete')
            store.complete(1)
        finally:await provider.close()
    start=time.perf_counter()
    async with httpx.AsyncClient(timeout=60,trust_env=False) as client:
        result=await client.post(endpoint+'/embeddings',headers={'Authorization':'Bearer '+embed_key},
            json={'model':args.embed_model,'input':['主体甲保留原文。','主体乙独立原文。'],
                  **({} if args.omit_dimensions else {'dimensions':args.embed_dimensions}),'encoding_format':'float'})
        body=result.json()
        summary['samples'].append({'kind':'embedding_endpoint_probe','model':args.embed_model,
            'status':result.status_code,'elapsed_s':time.perf_counter()-start,'response':body})
    (destination/'result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print(json.dumps({'output':str(destination),'samples':[{k:v for k,v in sample.items() if k in ('kind','status','elapsed_s','error_type')} for sample in summary['samples']]},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    credentials=parser.add_mutually_exclusive_group(required=True)
    credentials.add_argument('--key-file')
    credentials.add_argument('--env-file')
    parser.add_argument('--base-url',default='https://openrouter.ai/api/v1')
    parser.add_argument('--llm-model',default='qwen/qwen3.8-flash')
    parser.add_argument('--embed-model',default='qwen/qwen3-embedding-8b')
    parser.add_argument('--embed-dimensions',type=int,default=512)
    parser.add_argument('--omit-dimensions',action='store_true')
    parser.add_argument('--embedding-adapter-only',action='store_true')
    parser.add_argument('--output',required=True)
    asyncio.run(main(parser.parse_args()))
