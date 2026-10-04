"""重放已留证首请求一次，仅观察SDK逐chunk usage，不执行任何模型行动。"""
import argparse
import json
import os
from pathlib import Path
import time


def recorded_request(source):
    from society0.kernel.storage import StageReader
    from society0.kernel.threads import ThreadStore
    with StageReader(source) as reader:
        tid,seq=reader.read(lambda view:view.query(
            "SELECT thread_id,seq FROM thread_events WHERE kind='request' ORDER BY rowid LIMIT 1"))[0]
        saved=ThreadStore(reader).read_request(tid,seq)
    # 本探针限定首轮纯system/user输入，没有typed历史到wire的转换歧义。
    assert all(message.get('role') in ('system','user') for message in saved['messages'])
    allowed={'model','max_tokens','temperature','parallel_tool_calls','tools','tool_choice','extra_body'}
    assert not set(saved['provider_options'])-allowed,set(saved['provider_options'])-allowed
    return {'messages':saved['messages'],**saved['provider_options']}


def probe(client,request,output,*,continuous=False):
    output.mkdir(parents=True,exist_ok=False)
    (output/'request.json').write_text(json.dumps(request,ensure_ascii=False,indent=2))
    options={'include_usage':True}
    if continuous:options['continuous_usage_stats']=True
    start=time.perf_counter();chunks=[]
    with client.chat.completions.create(**request,stream=True,stream_options=options) as stream:
        for index,chunk in enumerate(stream):
            raw=chunk.model_dump(mode='json')
            chunks.append({'index':index,'usage':raw.get('usage'),
                'sdk_chunk_json_bytes':len(json.dumps(raw,ensure_ascii=False).encode()),
                'finish_reasons':[choice.get('finish_reason') for choice in raw.get('choices',[]) if choice.get('finish_reason') is not None]})
    reported=[chunk['usage'] for chunk in chunks if chunk['usage'] is not None]
    result={'chunks':chunks,'latency_s':time.perf_counter()-start,'usage_reports':len(reported),
        'last_usage':reported[-1] if reported else None,
        'sum_prompt_tokens':sum(row.get('prompt_tokens',0) for row in reported),
        'sum_completion_tokens':sum(row.get('completion_tokens',0) for row in reported),
        'sum_cache_hit_tokens':sum(row.get('prompt_cache_hit_tokens',0) for row in reported),
        'continuous_usage_stats':continuous,
        'request_json_bytes':len(json.dumps(request,ensure_ascii=False).encode()),
        'limits':'One physical SDK streaming call. No tool execution. Chunk bytes are reserialized SDK objects, not network byte counts. No assumption that chunk usage is delta or cumulative.'}
    (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--continuous',action='store_true');a=p.parse_args()
    request=recorded_request(a.source)
    from openai import OpenAI
    import httpx
    with OpenAI(base_url=os.environ['SOCIETY0_REAL_LLM_URL'],api_key=os.environ['SOCIETY0_REAL_LLM_KEY'],max_retries=0,timeout=60,
                http_client=httpx.Client(trust_env=False,timeout=60)) as client:
        result=probe(client,request,a.output,continuous=a.continuous)
    print(json.dumps({key:value for key,value in result.items() if key!='chunks'},ensure_ascii=False))
