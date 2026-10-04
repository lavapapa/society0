"""现有真实工件的离线验收；期望短语直接来自报告正文，保留全部标点。"""
import argparse
import json
from pathlib import Path
from society0.kernel.storage import StageReader
from society0.kernel.threads import ThreadStore


def review(path):
    from tests.e2e.test_core_next_real import assert_vfs_artifacts
    assert_vfs_artifacts(path)
    with StageReader(path) as reader:
        body=reader.read(lambda v:v.query('SELECT body FROM reports WHERE id=1'))[0][0]
        marker='\n核对短语：'
        assert body.count(marker)==1
        phrase=body.split(marker,1)[1]
        submission=reader.read(lambda v:v.query('SELECT actor,count,total,phrase FROM submissions'))
        assert submission==[('a',12,546,phrase)]
        threads=ThreadStore(reader);tid=threads.find('a',{'time':1,'phase':'decision'})
        assert threads.describe(tid)['status']=='completed'
        messages=threads.read_messages(tid)
        calls={call['id']:call for m in messages for call in m.get('tool_calls',[])}
        names=[c['function']['name'] for c in calls.values()]
        assert all(n in names for n in ('data_list','data_query','data_read','bash','action_find','action_describe','action_invoke'))
        assert names.count('data_read')>=2
        pages=[];outputs=[];reads=[]
        for message in messages:
            if message['role']!='tool':continue
            call=calls[message['tool_call_id']];args=json.loads(call['function']['arguments']);response=json.loads(message['content']);outputs.append(response)
            if call['function']['name']=='data_query' and args.get('path')=='/catalog/prices' and not response.get('error'):
                pages.append((args['query'],response))
            if call['function']['name']=='data_read' and args.get('path')=='/catalog/body/1':reads.append((args,response))
        assert len(pages)==4
        cursor=None
        for query,page in pages:
            assert query.get('cursor')==cursor and query['limit']==3 and page['total']==12
            cursor=page['next_cursor']
        assert cursor is None
        assert [r['id'] for _,p in pages for r in p['items']]==list(range(1,13))
        assert [r['amount'] for _,p in pages for r in p['items']]==[i*7 for i in range(1,13)]
        shell=next(v for v in outputs if 'stdout' in v)
        assert shell['exit_code']==0 and json.loads(shell['stdout'])=={'count':12,'total':546}
        offset=0;raw=bytearray()
        for args,result in reads:
            assert args['offset']==offset and args['size']==64 and args['expected_revision']==result['revision']==0
            assert result['encoding']=='utf-8' and result['total_bytes']==len(body.encode())
            raw.extend(result['data'].encode());offset=result['next_offset']
        assert offset is None and bytes(raw)==body.encode()
        return {'source':str(path),'thread_id':tid,'status':'passed_offline_artifact_review','expected_source':'reports.body after exact newline marker 核对短语：; no trimming','expected_phrase':phrase,'submission':submission,'query_pages':len(pages),'document_ranges':len(reads),'document_bytes':len(raw),'all_original_following_assertions':True,'extra_full_document_range_equivalence':True,'provider_calls_added':0}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=review(a.source);a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
