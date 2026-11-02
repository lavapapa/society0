"""规则驱动沿用原自动召回合同：自定义 recall_query 在无决策作用域时照常调用。
"""
from types import SimpleNamespace

import pytest

from society0.kernel.activation import ActivationContext, activation_scope
from society0.kernel.interaction import InteractionScope, Moment
from society0.kernel.memory import MEMORY_SCHEMA, Memory, MemoryExtension, MemoryPolicy
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
from tests.primary.test_kernel_memory import Client, Embed


@pytest.mark.asyncio
async def test_rule_activation_still_uses_custom_recall_query(tmp_path):
    store = StageStore.create(tmp_path / 'run', [*THREAD_SCHEMA, *MEMORY_SCHEMA])
    try:
        threads = ThreadStore(store)
        embed = Embed()
        asked = []
        async def recall_query(session):
            asked.append(session.actor.id)
            return 'known'
        memory = Memory(store, threads, embed=embed, client=Client(), recall_query=recall_query,
                        policy=MemoryPolicy(auto_recall=True, auto_write=False, active_tools=True))
        job = memory.prepare_job('a', None, 'seed', timestamp=0,
                                 entries=[{'content': 'known experience', 'importance': 3}])
        await memory.finish_job(job)

        moment = Moment(1, 'operate')
        scope = InteractionScope('a', moment)
        session = SimpleNamespace(step=1, actor=SimpleNamespace(id='a'), moment=moment,
                                  scope=scope, cursors={}, signals=())
        context = ActivationContext(session, None, 'rule')
        async with activation_scope(context, (MemoryExtension(memory),)):
            await context.prepare()
        assert asked == ['a'], '规则驱动没有决策作用域时仍调用装配提供的召回回调'
        assert any('known experience' in message['content'] for message in context.messages)
        assert any('memory_actions_target' in message['content'] for message in context.messages)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_auto_recall_without_callback_fails_at_activation(tmp_path):
    store = StageStore.create(tmp_path / 'run2', [*THREAD_SCHEMA, *MEMORY_SCHEMA])
    try:
        threads = ThreadStore(store)
        memory = Memory(store, threads, embed=Embed(), client=Client(),
                        policy=MemoryPolicy(auto_recall=True, auto_write=False, active_tools=False))
        moment = Moment(1, 'operate')
        session = SimpleNamespace(step=1, actor=SimpleNamespace(id='a'), moment=moment,
                                  scope=InteractionScope('a', moment), cursors={}, signals=())
        context = ActivationContext(session, None, 'rule')
        with pytest.raises(ValueError, match='recall_query'):
            async with activation_scope(context, (MemoryExtension(memory),)):
                await context.prepare()
    finally:
        store.close()
