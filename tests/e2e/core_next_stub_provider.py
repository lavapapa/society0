"""确定性 SDK/本地 HTTP 响应，仅检查真实验收脚本结构。"""
import json
import re


def chat_response(request):
    messages=request['messages'];tools=request.get('tools',[])
    names=[item['function']['name'] for item in tools]
    replies=[json.loads(m['content']) for m in messages if m['role']=='tool']
    user='\n'.join(str(m.get('content','')) for m in messages if m['role']=='user')
    name=None;args={}
    if '从共享根目录开始查找 catalog 信息' in user:
        name,args=vfs_call(messages)
    elif names==['extract_memories'] or (isinstance(request.get('tool_choice'),dict) and request['tool_choice']['function']['name']=='extract_memories'):
        name='extract_memories';args={'memories':[{'content':'重要经历：三吨原料每吨2000元；B42订单已经收款500元。','importance':4}]}
    elif 'submit_result' in names and '访谈测量' in user:
        name='submit_result';args={'score':8,'reason':'按明确测量任务回答'}
    elif '目标' in user and '"namespace"' in user:
        target=json.loads(re.search(r'\{"namespace"[^}]+\}',user).group())
        if target['namespace']=='chat':action='chat.send_message_to_partner';inner={'content':'共同讨论产业预期'}
        elif target['namespace']=='social':
            if 'get_trending_posts' in user:action='social.get_trending_posts';inner={}
            else:
                action='social.publish_post'
                number=re.findall(r'这是第(\d+)步',user)
                inner={'content':'这是第'+number[-1]+'步的完整更新' if number else '原料价格保持稳定','tags':[] if number else ['market']}
        elif 'value 为 9' in user:action='submit_measurement';inner={'value':9}
        else:action='record_observation';inner={'value':7}
        if not replies:name='action_find';args={'target':target,'query':'','limit':10,'cursor':None}
        elif len(replies)==1:name='action_describe';args={'name':action,'target':target}
        else:name='action_invoke';args={'name':action,'target':target,'arguments':inner}
    message={'role':'assistant','content':'B42订单已经收款500元。三吨原料每吨2000元。'}
    if name:message={'role':'assistant','content':None,'tool_calls':[{'id':'call-'+str(len(messages)), 'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
    return dict(id='fixture',model=request['model'],object='chat.completion',created=0,
        choices=[{'index':0,'finish_reason':'tool_calls' if name else 'stop','message':message}],
        usage={'prompt_tokens':10,'completion_tokens':5,'total_tokens':15})


def vfs_call(messages):
    import shlex
    calls={call['id']:call['function'] for message in messages for call in (message.get('tool_calls') or [])}
    history=[(calls[m['tool_call_id']]['name'],json.loads(calls[m['tool_call_id']]['arguments']),json.loads(m['content']))
             for m in messages if m['role']=='tool']
    listed=[args['path'] for name,args,result in history if name=='data_list']
    for path in ('/','/catalog'):
        if path not in listed:return 'data_list',{'path':path,'limit':100,'cursor':None}
    prices=[result for name,args,result in history if name=='data_query' and args['path']=='/catalog/prices']
    if not prices or prices[-1]['next_cursor'] is not None:
        return 'data_query',{'path':'/catalog/prices','query':{'limit':3,'cursor':prices[-1]['next_cursor'] if prices else None}}
    actors=[result for name,args,result in history if name=='data_query' and args['path']=='/catalog/actors']
    if not actors:return 'data_query',{'path':'/catalog/actors','query':{}}
    reports=[result for name,args,result in history if name=='data_query' and args['path']=='/catalog/reports']
    if not reports:return 'data_query',{'path':'/catalog/reports','query':{}}
    reference=reports[0]['items'][0]['body']
    reads=[result for name,args,result in history if name=='data_read']
    if not reads or reads[-1]['next_offset'] is not None:
        return 'data_read',{'path':reference['path'],'offset':reads[-1]['next_offset'] if reads else 0,
            'size':64,'encoding':'utf-8','expected_revision':reference['expected_revision']}
    rows=[row for page in prices for row in page['items']]
    if not any(name=='bash' for name,args,result in history):
        return 'bash',{'script':"printf '%s' "+shlex.quote(json.dumps(rows))+" | jq '{count:length,total:map(.amount)|add}'"}
    target=actors[0]['items'][0]['ref']
    if not any(name=='action_find' for name,args,result in history):
        return 'action_find',{'target':target,'query':'','limit':100,'cursor':None}
    if not any(name=='action_describe' for name,args,result in history):
        return 'action_describe',{'target':target,'name':'catalog.submit'}
    phrase=''.join(part['data'] for part in reads).split('\n核对短语：',1)[1]
    return 'action_invoke',{'target':target,'name':'catalog.submit','arguments':{'count':len(rows),'total':sum(row['amount'] for row in rows),'phrase':phrase}}
