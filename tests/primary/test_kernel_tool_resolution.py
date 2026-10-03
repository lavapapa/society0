"""物理工具策略的短决议与完整请求水位共同恢复。"""
from society0.kernel.storage import StageStore
from society0.kernel.threads import ThreadStore, THREAD_SCHEMA


def test_request_resolution_roundtrips_without_repeating_tools_or_messages(tmp_path):
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',1,'decision')
        threads.append_message(tid,{'role':'user','content':'完整原文'*10000})
        resolution={'policy':'auto_restrict','requested':'required','effective':'auto',
                    'selected_tool_name':'act','tools_filtered':True,'original_tools_count':2,'effective_tools_count':1}
        options={'model':'m','tools':[{'type':'function','function':{'name':'act','parameters':{'type':'object'}}}],'tool_choice':'auto'}
        seq=threads.record_provider_request(tid,provider_options=options,physical_request_id='p',tool_choice_resolution=resolution)
        request=threads.read_request(tid,seq)
        assert request['tool_choice_resolution']==resolution and request['provider_options']==options
        assert request['messages']==[{'role':'user','content':'完整原文'*10000}]
        raw=next(item for item in threads.tail(tid)['items'] if item['seq']==seq)['payload']
        assert 'messages' not in raw and 'tools' not in raw['tool_choice_resolution']
        store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as store:
        assert ThreadStore(store).read_request(tid,seq)==request
