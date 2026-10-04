"""离线脚本使用公开 typed 模型提供方合同，不接入物理网络。"""
from society0.kernel.model_messages import history


def scripted_response(threads, thread_id, response):
    message = {key: value for key, value in response.items()
               if key in ('role', 'content', 'tool_calls', 'reasoning_content')}
    message.setdefault('role', 'assistant')
    sequence = threads.append_message(thread_id, message)
    typed = history([message])[0]
    finish = response.get('finish_reason', 'stop')
    typed.finish_reason = 'tool_call' if finish == 'tool_calls' else finish
    incomplete = response.get('incomplete_reason') or ('output_token_limit' if finish == 'length' else None)
    return typed, sequence, incomplete


class TypedScriptProvider:
    def __init__(self, script, threads):
        self.script, self.threads = script, threads

    def __getattr__(self, name):
        return getattr(self.script, name)

    async def request_model(self, thread_id, options, *, model_messages=None):
        return scripted_response(self.threads, thread_id, await self.script.request(thread_id, options))


def bind_scripted_request(monkeypatch, provider_type, script):
    async def request_model(self, thread_id, options, *, model_messages=None):
        return scripted_response(self.threads, thread_id, await script(self, thread_id, options))
    monkeypatch.setattr(provider_type, 'request', script)
    monkeypatch.setattr(provider_type, 'request_model', request_model)
