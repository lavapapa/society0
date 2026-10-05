"""完整认知材料与按 Thread 持久定位的增量感知输入。"""
from dataclasses import dataclass
import json

from ..async_utils import invoke_maybe_async


@dataclass(frozen=True)
class InputBatch:
    messages: list[dict]
    consumer: str
    cursor: object
    context: dict | None = None


def _text(value):
    return value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,allow_nan=False)


class CognitiveInput:
    def __init__(self, threads, perception, *, consumer='operating_context', environment='',
                 precision=None, reminders=None):
        self.threads,self.perception=threads,perception
        self.consumer,self.environment=consumer,environment
        self.precision,self.reminders=precision,reminders

    async def __call__(self, session):
        tid=session.cursors['thread_id']
        saved=self.threads.input_cursor(tid,self.consumer)
        messages=[]
        record=session.actor.config
        environment=await invoke_maybe_async(self.environment,session) if callable(self.environment) else self.environment
        precision=await invoke_maybe_async(self.precision,session) if callable(self.precision) else self.precision
        context={'role':'system','content':'主体背景\n'+_text(record.persona)+'\n环境说明\n'+_text(environment)+'\n感知精度\n'+_text(precision)}
        if context == self.threads.input_context(tid,self.consumer):
            context=None
        # 每次实际激活读取的 ActorRecord 是当前主观状态，全文进入本次输入。
        messages.append({'role':'user','content':'当前主观状态\n'+_text(record.state)})
        additions,position=await invoke_maybe_async(self.perception,session,None if saved is None else saved['position'])
        messages.extend(additions)
        if session.signals:
            messages.append({'role':'user','content':'本次激活信号\n'+_text(list(session.signals))})
        if self.reminders is not None:
            reminders=await invoke_maybe_async(self.reminders,session) if callable(self.reminders) else self.reminders
            messages.append({'role':'user','content':'本次提醒\n'+_text(reminders)})
        return InputBatch(messages,self.consumer,{'position':position},context)
