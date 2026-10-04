"""驱动共用的有序认知扩展作用域；扩展使用标准异步上下文管理器。"""
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field

@dataclass
class ActivationContext:
    session: object
    thread_id: str | None = None
    kind: str = 'rule'
    messages: list = field(default_factory=list)
    inputs: object = None
    preparations: list = field(default_factory=list)
    mounts: dict = field(default_factory=dict)
    experience: object = None
    through: int | None = None
    result: object = None
    timings: dict = field(default_factory=dict)

    async def prepare(self):
        from ..async_utils import invoke_maybe_async
        for callback in self.preparations:
            await invoke_maybe_async(callback,self)

@asynccontextmanager
async def activation_scope(context, extensions):
    cursors=context.session.cursors
    previous=cursors.get('activation')
    cursors['activation']=context
    try:
        async with AsyncExitStack() as stack:
            for extension in extensions:
                await stack.enter_async_context(extension(context))
            yield context
    finally:
        if previous is None: cursors.pop('activation',None)
        else: cursors['activation']=previous
