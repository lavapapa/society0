"""真实 Bashkit 与共享交互服务的合同验收。"""
import asyncio
import base64
import json
import shlex

import pytest

pytest.importorskip('bashkit')
from society0.kernel.interaction import (
    Action, ActionResult, Actions, DocumentChunk, Information, InteractionScope,
    Moment, Page, Ref, ScopeClosed,
)
from society0.kernel.shell import ShellSession


def quoted(value):
    return shlex.quote(json.dumps(value, ensure_ascii=False))


class Documents:
    def __init__(self):
        self.calls = []
        self.body = '甲🙂乙，完整材料'.encode()
    def ref(self, path): return Ref('market', 'document', path)
    def list(self, scope, path, *, limit, cursor):
        self.calls.append(('list', scope.actor))
        return Page([{'path': '/market/doc', 'ref': self.ref('/market/doc')}], 1, None, 1)
    def read(self, scope, path, *, offset, size):
        self.calls.append(('read', scope.actor, offset, size))
        end = min(offset + size, len(self.body))
        return DocumentChunk(self.body[offset:end], len(self.body), end if end < len(self.body) else None, 1, self.ref(path))
    def query(self, scope, path, query):
        return Page([{'actor': scope.actor, 'price': 7}], 1, None, 1)


def build(tmp_path, actor='alice', snapshot=None):
    docs = Documents()
    info = Information(lambda scope, operation, ref: scope.actor == 'alice')
    info.mount('/market', docs)
    actions = Actions(lambda scope, operation, ref: scope.actor == 'alice')
    calls = []
    def buy(scope, target, args):
        calls.append(scope.actor)
        return ActionResult('completed', {'receipt': '原文' * args.get('count', 1)})
    actions.register(Action('buy', ('market', 'document'), '购买', {'type': 'object'}, buy, terminal=True))
    scope = InteractionScope(actor, Moment(0, 'trade'))
    shell = ShellSession(scope, info, actions, result_dir=tmp_path, workspace_snapshot=snapshot)
    return shell, docs, calls


@pytest.mark.asyncio
async def test_data_and_action_share_bound_identity_and_discoverable_root(tmp_path):
    shell, docs, calls = build(tmp_path)
    root = json.loads((await shell.execute('data list /')).stdout)
    assert root['total'] == 1 and root['items'][0]['path'] == '/market'
    listing = json.loads((await shell.execute('data list /market')).stdout)
    target = listing['items'][0]['ref']
    found = json.loads((await shell.execute('action find ' + quoted({'target': target}))).stdout)
    assert found['total'] == 1 and 'parameters' not in found['items'][0]
    described = json.loads((await shell.execute('action describe ' + quoted({'name': 'buy', 'target': target}))).stdout)
    assert described['parameters']['type'] == 'object'
    result = json.loads((await shell.execute('action invoke ' + quoted({'name': 'buy', 'target': target, 'arguments': {}}))).stdout)
    assert result['status'] == 'completed' and result['terminal']
    assert calls == ['alice']
    assert json.loads((await shell.execute('data query /market ' + quoted({'fields': ['price']}))).stdout)['total'] == 1
    await shell.aclose()


@pytest.mark.asyncio
async def test_actor_parameter_cannot_override_scope(tmp_path):
    shell, docs, calls = build(tmp_path, actor='bob')
    result = await shell.execute('action invoke ' + quoted({'actor': 'alice', 'name': 'buy',
        'target': {'namespace': 'market', 'kind': 'document', 'key': '/market/doc'}, 'arguments': {}}))
    assert result.exit_code != 0 and calls == []
    assert (await shell.execute('data read /market/doc')).exit_code != 0
    await shell.aclose()


@pytest.mark.asyncio
async def test_text_range_preserves_characters_and_binary_is_explicit(tmp_path):
    shell, docs, _ = build(tmp_path)
    offset, pieces = 0, []
    while True:
        response = json.loads((await shell.execute('data read /market/doc ' + quoted({'offset': offset, 'size': 4}))).stdout)
        assert response['encoding'] == 'utf-8' and response['revision'] == 1
        pieces.append(response['data'])
        if response['next_offset'] is None: break
        assert response['next_offset'] > offset
        offset = response['next_offset']
    assert ''.join(pieces).encode() == docs.body
    binary = json.loads((await shell.execute('data read /market/doc ' + quoted({'size': 4, 'encoding': 'base64'}))).stdout)
    assert base64.b64decode(binary['data']) == docs.body[:4]
    await shell.aclose()


@pytest.mark.asyncio
async def test_workspace_pipeline_snapshot_rebind_and_shell_state(tmp_path):
    shell, _, _ = build(tmp_path / 'first')
    result = await shell.execute('mkdir -p /workspace; cd /workspace; x=kept; printf "1\\n2\\n3\\n" > rows; cat rows | head -n 1; tail -n 1 rows; echo "{\"n\":2}" > ignored; false')
    assert result.exit_code == 1 and result.stdout == '1\n3\n'
    snapshot = shell.snapshot()
    await shell.aclose()
    restored, _, _ = build(tmp_path / 'second', actor='bob', snapshot=snapshot)
    assert (await restored.execute('pwd; echo $x; cat rows')).stdout == '/workspace\nkept\n1\n2\n3\n'
    assert (await restored.execute('data read /market/doc')).exit_code != 0
    assert (await restored.execute('rm rows; test ! -e rows')).exit_code == 0
    await restored.aclose()


@pytest.mark.asyncio
async def test_complete_large_action_receipt_and_stdout_are_read_without_reexecution(tmp_path):
    shell, _, calls = build(tmp_path)
    request = {'name': 'buy', 'target': {'namespace': 'market', 'kind': 'document', 'key': '/market/doc'}, 'arguments': {'count': 400000}}
    result = await shell.execute('action invoke ' + quoted(request))
    assert result.exit_code == 0 and result.stdout_truncated and not result.stderr_truncated
    assert result.stdout_ref and result.receipts
    offset, chunks = 0, []
    while True:
        chunk = shell.read_result(result.stdout_ref, offset=offset, size=65536, encoding='base64')
        chunks.append(base64.b64decode(chunk['data']))
        if chunk['next_offset'] is None: break
        offset = chunk['next_offset']
    full = json.loads(b''.join(chunks))
    assert full['value']['receipt'] == '原文' * 400000
    receipt = shell.read_result(result.receipts[0], size=4, encoding='base64')
    assert receipt['total_bytes'] > 1000000 and calls == ['alice']
    assert len(shell.snapshot()) < 10000
    await shell.aclose()


@pytest.mark.asyncio
async def test_output_exit_stderr_and_result_command(tmp_path):
    shell, _, _ = build(tmp_path)
    result = await shell.execute('echo diagnostic >&2; echo hello; exit 7')
    assert result.exit_code == 7 and result.stderr == 'diagnostic\n' and result.stdout == 'hello\n'
    read = json.loads((await shell.execute('result read ' + shlex.quote(result.stdout_ref))).stdout)
    assert read['data'] == 'hello\n' and read['next_offset'] is None
    await shell.aclose()


@pytest.mark.asyncio
async def test_cancel_async_callback_drains_and_closes_session(tmp_path):
    started, cleaned = asyncio.Event(), asyncio.Event()
    class Slow(Documents):
        async def read(self, scope, path, *, offset, size):
            started.set()
            try: await asyncio.Event().wait()
            finally: cleaned.set()
    info = Information(lambda *args: True)
    info.mount('/slow', Slow())
    scope = InteractionScope('alice', Moment(0, 'read'))
    shell = ShellSession(scope, info, Actions(lambda *args: True), result_dir=tmp_path)
    task = asyncio.create_task(shell.execute('data read /slow/doc'))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    await asyncio.wait_for(cleaned.wait(), 2)
    with pytest.raises(ScopeClosed): await shell.execute('echo again')
    await shell.aclose()


@pytest.mark.asyncio
async def test_handler_fault_is_not_swallowed_as_ordinary_shell_exit(tmp_path):
    info = Information(lambda *args: True)
    actions = Actions(lambda *args: True)
    def failed(*args): raise RuntimeError('partial world write')
    actions.register(Action('broken', ('m', 'job'), 'broken', {}, failed))
    shell = ShellSession(InteractionScope('alice', Moment(0, 'act')), info, actions, result_dir=tmp_path)
    with pytest.raises(RuntimeError, match='partial world write'):
        await shell.execute('action invoke ' + quoted({'name': 'broken', 'target': {'namespace': 'm', 'kind': 'job', 'key': '1'}, 'arguments': {}}))
    await shell.aclose()


@pytest.mark.asyncio
async def test_handler_value_error_propagates_and_later_actions_do_not_run(tmp_path):
    actions = Actions(lambda *args: True)
    calls = []
    def broken(*args):
        calls.append('broken')
        raise ValueError('state write failed')
    def later(*args):
        calls.append('later')
        return ActionResult('completed')
    for name, fn in [('broken', broken), ('later', later)]:
        actions.register(Action(name, ('m', 'job'), name, {}, fn))
    shell = ShellSession(InteractionScope('alice', Moment(0, 'act')), Information(lambda *args: True), actions, result_dir=tmp_path)
    def invoke(name):
        return 'action invoke ' + quoted({'name': name, 'target': {'namespace': 'm', 'kind': 'job', 'key': '1'}, 'arguments': {}})
    with pytest.raises(ValueError, match='state write failed'):
        await shell.execute(invoke('broken') + '; ' + invoke('later'))
    assert calls == ['broken']
    await shell.aclose()


@pytest.mark.asyncio
async def test_summary_has_exact_output_continuation_metadata(tmp_path):
    shell, _, _ = build(tmp_path)
    result = await shell.execute('for n in 1 2 3 4 5 6 7; do printf "%010000d" 1; printf "%010000d" 2 >&2; done')
    assert result.stdout_total_bytes == result.stderr_total_bytes == 70000
    assert result.stdout_next_offset == result.stderr_next_offset == 65536
    assert result.stdout_truncated and result.stderr_truncated
    assert shell.read_result(result.stderr_ref, offset=result.stderr_next_offset)['next_offset'] is None
    await shell.aclose()


@pytest.mark.asyncio
async def test_two_sessions_have_unambiguous_run_relative_result_refs(tmp_path):
    first, _, _ = build(tmp_path)
    second, _, _ = build(tmp_path)
    left = await first.execute('echo left')
    right = await second.execute('echo right')
    assert left.command_id == right.command_id == '1'
    assert left.stdout_ref != right.stdout_ref
    assert (tmp_path / left.stdout_ref).read_text() == 'left\n'
    assert (tmp_path / right.stdout_ref).read_text() == 'right\n'
    assert left.session_id != right.session_id
    with pytest.raises(ValueError, match='session'):
        second.read_result(left.stdout_ref)
    await first.aclose()
    assert (tmp_path / left.stdout_ref).read_text() == 'left\n'
    await second.aclose()


@pytest.mark.asyncio
async def test_shell_accepts_bound_driver_action_facade_for_all_three_entries(tmp_path):
    calls = []
    class Facade:
        async def find(self, target, **kwargs):
            calls.append('find')
            return Page([], 0, None, 1)
        async def describe(self, name, target):
            calls.append('describe')
            raise ValueError('filtered by driver')
        async def invoke(self, name, target, arguments):
            calls.append('invoke')
            return ActionResult('rejected', {'reason': 'driver_policy'})
    shell = ShellSession(InteractionScope('a', Moment(0, 'p')), Information(lambda *args: True),
                         bound_actions=Facade(), result_dir=tmp_path)
    target = {'namespace': 'm', 'kind': 'job', 'key': '1'}
    assert json.loads((await shell.execute('action find ' + quoted({'target': target}))).stdout)['total'] == 0
    assert (await shell.execute('action describe ' + quoted({'target': target, 'name': 'x'}))).exit_code != 0
    result = json.loads((await shell.execute('action invoke ' + quoted({'target': target, 'name': 'x', 'arguments': {}}))).stdout)
    assert result['status'] == 'rejected' and calls == ['find', 'describe', 'invoke']
    await shell.aclose()
