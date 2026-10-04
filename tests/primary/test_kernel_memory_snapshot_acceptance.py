"""标准提取器在物理请求前释放传入视图，自定义提取器仍取得完整原文。"""
import json
import weakref

import pytest

from society0.kernel.memory import ThreadMemoryExtractor
from tests.primary.test_kernel_memory import setup


@pytest.mark.asyncio
@pytest.mark.parametrize('standard', [True, False])
async def test_extraction_snapshot_ownership_matches_consumer(tmp_path, monkeypatch, standard):
    store, threads, thread, memory, _, _ = setup(tmp_path)
    original = [{'role': 'system', 'content': '完整背景'},
                {'role': 'user', 'content': '完整亲身经历🙂' * 10000}]
    for message in original:
        threads.append_message(thread, message)
    references = []
    native = threads.snapshot_messages

    class SnapshotMessages(list):
        pass

    def snapshot(*args, **kwargs):
        value = native(*args, **kwargs)
        messages = SnapshotMessages(value['messages'])
        references.append(weakref.ref(messages))
        return {**value, 'messages': messages}

    monkeypatch.setattr(threads, 'snapshot_messages', snapshot)

    class Provider:
        async def request(self, tid, options):
            assert references[-1]() is None
            assert threads.read_messages(tid)[:len(original)] == original
            return {'role': 'assistant', 'content': None, 'finish_reason': 'tool_calls',
                    'tool_calls': [{'id': 'memory', 'type': 'function', 'function': {
                        'name': 'extract_memories', 'arguments': json.dumps({'memories': []})}}]}

    async def custom(actor, tid, messages, *, metadata):
        assert references[-1]() is messages
        assert messages == original
        return []

    memory.extract = ThreadMemoryExtractor(threads, Provider()) if standard else custom
    try:
        job = await memory.extract_job('a', thread, through=threads.describe(thread)['last_seq'], timestamp=1)
        assert job
        assert references[-1]() is None
    finally:
        await memory.close()
        store.close()
