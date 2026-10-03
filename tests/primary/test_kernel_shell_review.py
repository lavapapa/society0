"""非作者复验：通过公开 shell 合同观察行为。"""
import asyncio
import base64
import json
import shlex

import pytest

pytest.importorskip('bashkit')
from society0.kernel.interaction import Actions, Action, ActionResult, Information, InteractionScope, Moment, Ref, ScopeClosed, DocumentChunk
from society0.kernel.shell import ShellSession


def shell(tmp_path, *, actions=None, scope=None, preview_bytes=4):
    return ShellSession(scope or InteractionScope('a', Moment(1, 'p')),
                        Information(lambda *a: True), actions or Actions(lambda *a: True),
                        result_dir=tmp_path, preview_bytes=preview_bytes)


@pytest.mark.asyncio
async def test_review_utf8_preview_and_all_ranges_preserve_native_full_output(tmp_path):
    session = shell(tmp_path, preview_bytes=5)
    try:
        result = await session.execute("printf '甲🙂乙甲🙂乙'")
        assert result.stdout == '甲'
        assert result.stdout_total_bytes == len('甲🙂乙甲🙂乙'.encode())
        assert result.stdout_next_offset == 3
        offset, pieces = 0, []
        while True:
            part = session.read_result(result.stdout_ref, offset=offset, size=5)
            pieces.append(part['data'])
            if part['next_offset'] is None:
                break
            assert part['next_offset'] > offset
            offset = part['next_offset']
        assert ''.join(pieces) == '甲🙂乙甲🙂乙'
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_review_native_binary_replacement_is_explicit_upstream_boundary(tmp_path):
    session = shell(tmp_path)
    try:
        result = await session.execute(r"printf '\377\000A'")
        assert result.exit_code == 0
        part = session.read_result(result.stdout_ref, encoding='base64')
        assert base64.b64decode(part['data']) == '\ufffd\x00A'.encode('utf-8')
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_review_closed_scope_blocks_next_action_without_hiding_first_effect(tmp_path):
    scope = InteractionScope('a', Moment(1, 'p'))
    actions = Actions(lambda *a: True)
    events = []
    def first(*args):
        events.append('first')
        scope.close()
        return ActionResult('completed', 'kept')
    def second(*args):
        events.append('second')
        return ActionResult('completed')
    for name, handler in [('first', first), ('second', second)]:
        actions.register(Action(name, ('m', 'thing'), '', {}, handler))
    session = shell(tmp_path, actions=actions, scope=scope)
    def invoke(name):
        return 'action invoke ' + shlex.quote(json.dumps({'name': name, 'target': {'namespace': 'm', 'kind': 'thing', 'key': '1'}, 'arguments': {}}))
    try:
        with pytest.raises(ScopeClosed):
            await session.execute(invoke('first') + '; ' + invoke('second'))
        assert events == ['first']
        receipts = list(session.result_dir.glob('receipt-*.json'))
        assert len(receipts) == 1
        assert json.loads(receipts[0].read_text())['value'] == 'kept'
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_review_binary_information_base64_keeps_original_bytes(tmp_path):
    class Binary:
        def ref(self, path):
            return Ref('binary', 'document', path)
        def read(self, scope, path, *, offset, size):
            body = b'\xff\x00A'
            data = body[offset:offset+size]
            return DocumentChunk(data, len(body), None, 1, self.ref(path))
    info = Information(lambda *a: True)
    info.mount('/binary', Binary())
    session = ShellSession(InteractionScope('a', Moment(1, 'p')), info,
                           Actions(lambda *a: True), result_dir=tmp_path)
    try:
        output = await session.execute('data read /binary/file \'{"encoding":"base64"}\'')
        assert base64.b64decode(json.loads(output.stdout)['data']) == b'\xff\x00A'
    finally:
        await session.aclose()
