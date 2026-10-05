"""Thread 和记忆的标准服务装配；可选依赖在所选工厂内导入。"""
from .plugins import Plugin


def thread_plugin(*, storage=('storage', 'store'), name='threads'):
    """Thread 与权威存储共用生命周期，无额外连接或后台任务。"""
    from .threads import THREAD_SCHEMA, ThreadStore
    def install(context):
        context.provide('threads', ThreadStore(context.require(*storage)))
    return Plugin(name, (storage[0],), install, schema=THREAD_SCHEMA)


def memory_plugin(*, client, embedding, extraction=None, extraction_options=None, policy=None,
                  recall_query=None, policy_selector=None, decay_rate=0.01, recall_top_k=10,
                  storage=('storage', 'store'), threads=('threads', 'threads'),
                  actions=('interaction', 'actions'), name='memory'):
    """具名模型配置三元组；向量客户端由声明的依赖插件管理。"""
    from .memory import MEMORY_SCHEMA, Memory, MemoryPolicy, ThreadMemoryExtractor
    policy = policy or MemoryPolicy()
    if policy_selector is None and policy.auto_recall and recall_query is None:
        raise ValueError('automatic memory recall requires recall_query')
    dependencies = [storage, threads, client, embedding]
    if extraction is not None:
        dependencies.append(extraction)
    if policy.active_tools or policy_selector is not None:
        dependencies.append(actions)

    def install(context):
        thread_store = context.require(*threads)
        embed = context.require(*embedding[:2])[embedding[2]]
        extractor = None
        if extraction is not None:
            provider = context.require(*extraction[:2])[extraction[2]]
            extractor = ThreadMemoryExtractor(thread_store, provider, request_options=extraction_options)
        memory = Memory(context.require(*storage), thread_store, embed=embed.embed,
                        client=context.require(*client), extract=extractor, policy=policy,
                        recall_query=recall_query, policy_selector=policy_selector, decay_rate=decay_rate, recall_top_k=recall_top_k)
        context.on_close(memory.close)
        if policy.active_tools or policy_selector is not None:
            registry = context.require(*actions)
            for action in memory.actions():
                registry.register(action)
        context.provide('memory', memory)

    return Plugin(name, tuple(dict.fromkeys(item[0] for item in dependencies)),
                  install, schema=MEMORY_SCHEMA)
