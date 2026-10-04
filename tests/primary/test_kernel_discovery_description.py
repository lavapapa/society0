"""真实 Driver 提供方请求中的发现说明必须反映公共查询合同。"""
import pytest
from tests.primary.test_kernel_llm import setup,reply
from society0.kernel.llm import LLMPolicy

@pytest.mark.asyncio
@pytest.mark.parametrize('parallel',[False,True])
async def test_provider_receives_literal_search_and_policy_specific_call_guidance(tmp_path,parallel):
    store,threads,provider,driver,session,_=setup(tmp_path,[reply(text='done')],policy=LLMPolicy(parallel_tool_calls=parallel))
    try:
        assert (await driver.run(session)).status=='completed'
        options=provider.requests[0][1]
        tools={tool['function']['name']:tool['function'] for tool in options['tools']}
        find=tools['action_find'];description=find['description']
        query=find['parameters']['properties']['query']['description']
        for text in (description,query):
            assert 'literal substring' in text and 'empty string' in text
            assert 'wildcard' in text and 'semantic' in text
        for tool in tools.values():
            assert ('at most one tool call per response' in tool['description']) is (not parallel)
        assert options['parallel_tool_calls'] is parallel
    finally:store.close()
