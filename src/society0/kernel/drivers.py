"""规则与模型驱动工厂通过普通 Plugin 服务装配。"""
from ..async_utils import invoke_maybe_async
from .activation import ActivationContext, activation_scope
from .plugins import Plugin

class RuleDriver:
    """规则可通过 session.cursors['activation'] 提供实际结构化经历。"""
    def __init__(self,run,*,extensions=()):
        self._run,self.extensions=run,tuple(extensions)

    async def run(self,session):
        context=ActivationContext(session)
        async with activation_scope(context,self.extensions):
            await context.prepare()
            result=await invoke_maybe_async(self._run,session)
            context.result=result
        return result


def rule_driver_plugin(run,*,extensions=(),name='rule_driver',requires=()):
    """扩展为服务引用或已装配的异步上下文管理器工厂。"""
    extensions=tuple(extensions)
    references=tuple(item for item in extensions if isinstance(item,tuple))
    def install(context):
        configured=tuple(context.require(*item) if isinstance(item,tuple) else item for item in extensions)
        context.provide('factory',lambda record:RuleDriver(run,extensions=configured))
    return Plugin(name,tuple(dict.fromkeys((*requires,*(item[0] for item in references)))),install)


def llm_driver_plugin(*,provider,threads=('threads','threads'),input_builder,policy=None,
                      extensions=(),shell_factory=None,provider_selector=None,name='llm_driver',requires=()):
    """共享服务在安装期取得；模型驱动在主体实际激活时创建。"""
    extensions=tuple(extensions)
    references=(provider,threads,*((input_builder,) if isinstance(input_builder,tuple) else ()),*(item for item in extensions if isinstance(item,tuple)))
    def install(context):
        from .llm import LLMDriver
        selected=context.require(*provider[:2])
        if len(provider)==3: selected=selected[provider[2]]
        thread_store=context.require(*threads)
        builder=context.require(*input_builder) if isinstance(input_builder,tuple) else input_builder
        configured=tuple(context.require(*item) if isinstance(item,tuple) else item for item in extensions)
        context.provide('factory',lambda record:LLMDriver(selected,thread_store,input_builder=builder,
            policy=policy,extensions=configured,shell_factory=shell_factory,provider_selector=provider_selector))
    return Plugin(name,tuple(dict.fromkeys((*requires,*(item[0] for item in references)))),install)
