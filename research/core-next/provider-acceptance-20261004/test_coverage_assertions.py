"""验收必须区分主体回答、输入答案与后置提取文本。"""
from tests.e2e import test_core_next_real as suite
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA,ThreadStore


def test_decision_answer_excludes_user_and_later_memory_extraction(tmp_path):
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',{'time':2,'phase':'decision'},'decision')
        threads.append_message(tid,{'role':'user','content':'召回材料：B42订单已收500元。'})
        threads.append_message(tid,{'role':'assistant','content':'资料不足，无法回答。'})
        threads.record_request(tid,provider_options={'tools':[{'type':'function','function':{'name':'extract_memories'}}]},physical_request_id='extract')
        threads.append_message(tid,{'role':'assistant','content':'记忆提取：B42已收500元。'})
        assert suite.decision_answer(store,'a',2)=='资料不足，无法回答。'


def test_factual_answer_rejects_merely_missing_or_wrong_fact():
    import pytest
    with pytest.raises(AssertionError):suite.assert_order_answer('B420订单已收5000元')
    with pytest.raises(AssertionError):suite.assert_order_answer('B42订单，金额不详')
    suite.assert_order_answer('订单代码B42，已收款金额为500元。')
