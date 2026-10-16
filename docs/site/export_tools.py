"""只构造LLMDriver工具声明；不安装资源、不建立网络连接。"""
import json
from society0.kernel.llm import LLMDriver, LLMPolicy

measurement = {'type': 'object', 'properties': {'answer': {'type': 'string'}},
               'required': ['answer'], 'additionalProperties': False}
variants = {
    'decision': (LLMPolicy(), None),
    'strict': (LLMPolicy(strict_tools=True), None),
    'decision-shell': (LLMPolicy(), lambda session: None),
    'interview': (LLMPolicy(mode='interview', result_schema=measurement), None),
    'direct-json': (LLMPolicy(result_schema=measurement, direct_json=True), None),
}
schemas = {}
for name, (policy, shell_factory) in variants.items():
    driver = LLMDriver(None, None, input_builder=None, policy=policy, shell_factory=shell_factory)
    schemas[name] = driver._tools()
assert schemas['direct-json'] == []
assert [tool['function']['name'] for tool in schemas['interview']] == ['submit_result']
assert len(schemas['decision-shell']) == len(schemas['decision']) + 1
print(json.dumps(schemas, ensure_ascii=False))
