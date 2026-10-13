"""真实提供方公共 runner 入口；任务和参数冻结，凭据来自显式环境。"""
from dataclasses import replace
from .core_next_composition_sdk import build_with_profile
from .core_next_real_support import configuration


def build(config):
    actual=configuration(embedding=False)
    if config['release']['commit']!=actual['release']:
        raise ValueError('release.commit must match SOCIETY0_REAL_RELEASE')
    task=('阅读本轮完整社交与通知材料，通过 submit_result 返回结构化测量。measurement 使用一句非空中文概括本轮感受。'
          if config.get('variant')=='interview' else
          '阅读本轮完整社交与通知材料，发布一条新动态表达你的判断。先发现并描述可用社交行动，'
          '再使用实际行动发布，正文保留你认为重要的完整事实，完成后自然结束本轮决定。')
    plan=build_with_profile({**config,'task':task},actual['llm'],offline=False)
    return replace(plan,contract=replace(plan.contract,
        configuration={**plan.contract.configuration,'provider':'real'},
        credential_env=('CODEX_HOME',) if actual['llm']['endpoints'][0]['provider_type']=='openai-codex' else ('SOCIETY0_REAL_LLM_KEY',)))
