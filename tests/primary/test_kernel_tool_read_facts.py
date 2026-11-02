"""通用完整读取事实游标：同激活累计、写动作保留、跨激活重置与字节覆盖。"""
from dataclasses import asdict

import pytest
from society0.kernel.interaction import Action, ActionResult, Ref
from society0.kernel.llm import _Ledger
from tests.primary.test_kernel_llm import setup, reply, invoke


@pytest.mark.asyncio
async def test_completed_action_facts_survive_write_and_reset_next_activation(tmp_path):
    ref = Ref('test', 'document', 'one')
    store, threads, provider, driver, session, calls = setup(tmp_path,
        [reply(invoke(name='read_document')), reply(invoke(identifier='write-2')), reply(text='done'), reply(text='again')])
    try:
        session.actions.actions.register(Action('read_document', ('m', 'job'), 'Read original',
            {'type': 'object'}, lambda *args: ActionResult('completed', {'facts': [asdict(ref)]}), read_only=True))
        assert (await driver.run(session)).status=='completed'
        assert session.cursors['read_facts']=={ref}
        thread = session.cursors['thread_id']
        assert (await driver.run(session)).status=='completed'
        assert session.cursors['thread_id']==thread
        assert session.cursors['read_facts']==set()
    finally:
        store.close()


@pytest.mark.parametrize('ranges,expected', [
    ([(0, 10, 1)], True), ([(5, 10, 1)], False),
    ([(0, 3, 1), (4, 10, 1)], False),
    ([(5, 10, 1), (0, 6, 1)], True),
    ([(0, 5, 1), (5, 10, 2)], False),
    ([(0, 5, 1), (0, 5, 1)], False),
])
def test_file_ranges_require_full_same_revision_coverage(tmp_path, ranges, expected):
    store, threads, provider, driver, session, calls = setup(tmp_path, [])
    try:
        ledger = _Ledger(driver, session, 'unused')
        ref = Ref('test', 'body', 'one')
        for start, stop, revision in ranges:
            ledger.read_effect(dict(source=ref, revision=revision, total_bytes=10,
                next_offset=stop if stop<10 else None), start)
        assert (ref in session.cursors['read_facts']) is expected
    finally:
        store.close()


@pytest.mark.parametrize('status', ['rejected', 'accepted'])
def test_noncompleted_action_does_not_publish_facts(tmp_path, status):
    store, threads, provider, driver, session, calls = setup(tmp_path, [])
    try:
        ledger = _Ledger(driver, session, 'unused')
        ledger.apply_effect(dict(status=status, facts=[asdict(Ref('test','body','one'))]))
        assert session.cursors['read_facts']==set()
    finally:
        store.close()
