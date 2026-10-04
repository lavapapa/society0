"""合成端点合同与缓存 usage 探针；凭据仅在显式 CLI 执行时从环境取得。"""
import argparse
import json
import os
from pathlib import Path
import time


def probe(client,*,llm,embedding,output):
    output.mkdir(parents=True,exist_ok=True)
    records=[]
    start=time.perf_counter()
    embedded=client.embeddings.create(model=embedding,input=['主体甲采购三吨铜材，每吨两千元。','主体乙交付两件独立设备，价款七百元。']).model_dump(mode='json')
    (output/'embedding-response.json').write_text(json.dumps(embedded,ensure_ascii=False))
    summary={'embedding':{'model':embedding,'latency_s':time.perf_counter()-start,
        'indices':[row['index'] for row in embedded['data']],
        'dimensions':[len(row['embedding']) for row in embedded['data']], 'usage':embedded.get('usage')},'requests':records}
    if sorted(summary['embedding']['indices'])!=[0,1]:raise ValueError('embedding index contract failed')
    tools=[{'type':'function','function':{'name':'read_probe','description':'读取本次合成测试值。',
        'parameters':{'type':'object','properties':{},'additionalProperties':False}}}]
    def request(label,messages,with_tools=False):
        kwargs={'model':llm,'messages':messages,'temperature':0,'max_tokens':256,
                'extra_body':{'enable_thinking':False}}
        if with_tools:kwargs.update(tools=tools,parallel_tool_calls=False,tool_choice='auto')
        before=time.perf_counter();response=client.chat.completions.create(**kwargs).model_dump(mode='json')
        elapsed=time.perf_counter()-before
        (output/(label+'-response.json')).write_text(json.dumps(response,ensure_ascii=False))
        records.append({'label':label,'latency_s':elapsed,'usage':response.get('usage'),
                        'finish_reason':response['choices'][0]['finish_reason']})
        return response['choices'][0]['message']
    messages=[{'role':'system','content':'遵照任务进行合成接口测试，回答简短。'},
              {'role':'user','content':'请调用 read_probe 获取实际值，再根据工具返回值回答。'}]
    first=request('tool-first',messages,True)
    calls=first.get('tool_calls') or []
    if len(calls)!=1 or calls[0]['function']['name']!='read_probe':
        raise ValueError('expected exactly one declared tool call; raw response retained')
    if json.loads(calls[0]['function']['arguments'])!={}:raise ValueError('unexpected tool arguments')
    # 只传协议字段；原始响应文件保留 SDK 的其他返回字段。
    assistant={key:first[key] for key in ('role','content','tool_calls') if key in first}
    final=request('tool-result',messages+[assistant,{'role':'tool','tool_call_id':calls[0]['id'],'content':'{"value":"PROBE-37"}'}],True)
    if 'PROBE-37' not in str(final.get('content')):raise ValueError('tool result was not consumed')
    prefix='\n'.join(f'合成记录{i:03d}：编号对应独立资料，保留顺序，当前状态为已登记。' for i in range(64))
    cache_messages=[{'role':'system','content':'下面是合成接口材料。回答一句“收到”即可。\n'+prefix},
                    {'role':'user','content':'请确认已收到材料。'}]
    request('prefix-first',cache_messages)
    request('prefix-repeat',cache_messages)
    request('prefix-new-tail',cache_messages+[{'role':'user','content':'新增尾消息：这次仍回答收到。'}])
    summary['limits']='三次长前缀为合成缓存探针，不涉及业务行动；实际token以usage为准；不根据延迟推断缓存命中。'
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    return summary


def cache_only(client,*,llm,output):
    """约8k输入token，两次完全相同；实际规模及命中由usage确认。"""
    output.mkdir(parents=True,exist_ok=True)
    prefix='\n'.join(f'合成记录{i:03d}：编号对应独立资料，保留顺序，当前状态为已登记。' for i in range(384))
    messages=[{'role':'system','content':'下面是合成接口材料。回答一句“收到”即可。\n'+prefix},
              {'role':'user','content':'请确认已收到材料。'}]
    rows=[]
    for label in ('long-first','long-repeat'):
        start=time.perf_counter()
        response=client.chat.completions.create(model=llm,messages=messages,max_tokens=128,
            temperature=0,extra_body={'enable_thinking':False}).model_dump(mode='json')
        elapsed=time.perf_counter()-start
        (output/(label+'-response.json')).write_text(json.dumps(response,ensure_ascii=False))
        rows.append({'label':label,'latency_s':elapsed,'usage':response.get('usage')})
    result={'requests':rows,'limits':'仅两次合成前缀；未发送cache开关；按usage判断，不按延迟推断。费用未知，不假定免费。'}
    (output/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--cache-only',action='store_true')
    args=parser.parse_args()
    if args.output.exists():parser.error('use a new output directory')
    from openai import OpenAI
    with OpenAI(base_url=os.environ['SOCIETY0_REAL_LLM_URL'],api_key=os.environ['SOCIETY0_REAL_LLM_KEY'],max_retries=0,timeout=60) as client:
        result=(cache_only(client,llm=os.environ['SOCIETY0_REAL_LLM_MODEL'],output=args.output) if args.cache_only else
                probe(client,llm=os.environ['SOCIETY0_REAL_LLM_MODEL'],embedding=os.environ['SOCIETY0_REAL_EMBED_MODEL'],output=args.output))
    print(json.dumps(result,ensure_ascii=False))
