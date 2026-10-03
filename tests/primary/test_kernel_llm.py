"""新版完整 LLM 循环：假提供方与真实 Thread/Action 消费者。"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from society0.kernel.interaction import Action, ActionResult, Actions, Information, InteractionScope, Moment, Ref
from society0.kernel.runtime import Actor, Session
from society0.kernel.storage import StageStore
from society0.kernel.threads import ThreadStore, THREAD_SCHEMA
from society0.kernel.llm import LLMDriver, LLMPolicy, current_action_call_id
from society0.kernel.models import ModelProvider


def call(identifier, tool, arguments):
    return {'id': identifier, 'type': 'function', 'function': {'name': tool, 'arguments': json.dumps(arguments)}}


def invoke(identifier='c1', name='work', arguments=None):
    return call(identifier, 'action_invoke', {'name': name, 'target': {'namespace': 'm', 'kind': 'job', 'key': '1'},
                                            'arguments': json.dumps(arguments or {})})


def reply(*calls, text='', finish=None):
    return {'role': 'assistant', 'content': text, 'tool_calls': list(calls),
            'finish_reason': finish or ('tool_calls' if calls else 'stop')}


class FakeProvider:
    def __init__(self, threads, replies):
        self.threads, self.replies = threads, list(replies)
        self.requests = []

    async def request(self, thread_id, options):
        messages = self.threads.read_messages(thread_id)
        self.requests.append((thread_id, options, messages))
        self.threads.record_request(thread_id, provider_options=options, physical_request_id=str(len(self.requests)))
        response = self.replies.pop(0)
        if isinstance(response, Exception):
            raise response
        self.threads.event(thread_id, 'provider_response', response)
        return response


def setup(tmp_path, replies, *, policy=None, handler=None, terminal=False, tags=(), schema=None):
    store = StageStore.create(tmp_path / 'run', THREAD_SCHEMA)
    threads = ThreadStore(store)
    provider = FakeProvider(threads, replies)
    calls = []
    def work(scope, target, arguments):
        calls.append((arguments, current_action_call_id.get()))
        return handler(scope, target, arguments) if handler else ActionResult('completed', {'original': '完整原文'})
    actions = Actions(lambda *a: True)
    actions.register(Action('work', ('m', 'job'), 'work', schema or {'type': 'object'}, work,
                            terminal=terminal, tags=tags))
    driver = LLMDriver(provider, threads, input_builder=lambda session: [
        {'role': 'system', 'content': 'persona precision reminders 完整内容'},
        {'role': 'user', 'content': 'current context 完整原文'},
    ], policy=policy or LLMPolicy())
    actor = Actor('a', driver)
    scope = InteractionScope('a', Moment(1, 'p'))
    session = Session(actor, scope, Information(lambda *a: True).bound(scope), actions.bound(scope), {}, None, (), SimpleNamespace(prepare_artifact=store.prepare_artifact))
    return store, threads, provider, driver, session, calls


@pytest.mark.asyncio
async def test_natural_finish_preserves_complete_input_and_thread(tmp_path):
    store, threads, provider, driver, session, calls = setup(tmp_path, [reply(text='finished')])
    try:
        result = await driver.run(session)
        assert result.status == 'completed' and not calls
        assert provider.requests[0][2][0]['content'] == 'persona precision reminders 完整内容'
        assert threads.read_messages(session.cursors['thread_id'])[-1]['content'] == 'finished'
        assert threads.describe(session.cursors['thread_id'])['status'] == 'completed'
    finally:
        store.close()


@pytest.mark.asyncio
async def test_terminal_completed_ends_without_extra_request(tmp_path):
    store, _, provider, driver, session, calls = setup(tmp_path, [reply(invoke())], terminal=True)
    try:
        assert (await driver.run(session)).status == 'completed'
        assert len(provider.requests) == len(calls) == 1
        assert calls[0][1] == 'c1'
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['accepted', 'rejected'])
async def test_noncompleted_terminal_result_does_not_complete_activation(tmp_path, outcome):
    store, _, provider, driver, session, calls = setup(tmp_path, [reply(invoke()), reply(text='finish')],
        terminal=True, handler=lambda *a: ActionResult(outcome))
    try:
        assert (await driver.run(session)).status == 'completed'
        assert len(provider.requests) == 2
    finally:
        store.close()


@pytest.mark.asyncio
async def test_required_corrections_continue_within_budget(tmp_path):
    store, _, provider, driver, session, calls = setup(tmp_path,
        [reply(text='early1'), reply(text='early2'), reply(invoke())], terminal=True,
        policy=LLMPolicy(required_names=('work',), max_turns=4))
    try:
        assert (await driver.run(session)).status == 'completed'
        assert len(provider.requests) == 3 and len(calls) == 1
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('finish', ['length', 'budget'])
async def test_hard_limit_never_executes_truncated_tool_or_requests_closing(tmp_path, finish):
    responses = [reply(invoke(), finish='length')] if finish == 'length' else [reply(invoke())]
    store, threads, provider, driver, session, calls = setup(tmp_path, responses,
        policy=LLMPolicy(max_action_calls=1))
    try:
        result = await driver.run(session)
        assert result.status == 'incomplete'
        assert result.reason == ('output_token_limit' if finish == 'length' else 'action_budget_exhausted')
        assert len(provider.requests) == 1
        assert len(calls) == (0 if finish == 'length' else 1)
        assert threads.describe(session.cursors['thread_id'])['status'] == 'incomplete'
    finally:
        store.close()


@pytest.mark.asyncio
async def test_batch_budget_rejects_all_before_first_domain_effect(tmp_path):
    store, _, provider, driver, session, calls = setup(tmp_path, [reply(invoke('one'), invoke('two'))],
        policy=LLMPolicy(max_action_calls=1, parallel_tool_calls=True))
    try:
        assert (await driver.run(session)).status == 'incomplete'
        assert not calls and len(provider.requests) == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_duplicate_call_id_reuses_receipt_without_budget_or_effect(tmp_path):
    store, threads, provider, driver, session, calls = setup(tmp_path,
        [reply(invoke('same')), reply(invoke('same')), reply(text='done')],
        policy=LLMPolicy(max_action_calls=2))
    try:
        assert (await driver.run(session)).status == 'completed'
        assert len(calls) == 1
        tools = [m for m in threads.read_messages(session.cursors['thread_id']) if m['role'] == 'tool']
        assert len(tools) == 2 and tools[0]['content'] == tools[1]['content']
    finally:
        store.close()


@pytest.mark.asyncio
async def test_parallel_false_has_one_corrective_turn_then_contract_failure(tmp_path):
    pair = reply(invoke('a'), invoke('b'))
    store, _, provider, driver, session, calls = setup(tmp_path, [pair, pair],
        policy=LLMPolicy(parallel_tool_calls=False))
    try:
        result = await driver.run(session)
        assert result.status == 'incomplete' and result.reason == 'parallel_tool_contract'
        assert len(provider.requests) == 2 and not calls
    finally:
        store.close()


@pytest.mark.asyncio
async def test_invalid_domain_schema_consumes_attempt_without_effect(tmp_path):
    store, _, provider, driver, session, calls = setup(tmp_path, [reply(invoke(arguments={'value': 'bad'}))],
        policy=LLMPolicy(max_action_calls=1), schema={'type': 'object', 'properties': {'value': {'type': 'integer'}}})
    try:
        assert (await driver.run(session)).status == 'incomplete'
        assert len(provider.requests) == 1 and not calls
    finally:
        store.close()


@pytest.mark.asyncio
async def test_partial_domain_error_propagates_without_next_model_call(tmp_path):
    def broken(*a):
        raise ValueError('partial domain write')
    store, threads, provider, driver, session, calls = setup(tmp_path, [reply(invoke())], handler=broken)
    try:
        with pytest.raises(ValueError, match='partial domain write'):
            await driver.run(session)
        assert len(calls) == len(provider.requests) == 1
        assert threads.describe(session.cursors['thread_id'])['status'] == 'incomplete'
    finally:
        store.close()


@pytest.mark.asyncio
async def test_reactivation_same_thread_complete_history_and_provider_session(tmp_path):
    store, threads, provider, driver, session, calls = setup(tmp_path,
        [reply(invoke()), reply(text='done'), reply(text='again')])
    try:
        await driver.run(session)
        thread_id = session.cursors['thread_id']
        old = threads.read_messages(thread_id)
        provider_session = threads.describe(thread_id)['provider_session_id']
        await driver.run(session)
        assert session.cursors['thread_id'] == thread_id
        assert provider.requests[-1][2][:len(old)] == old
        assert all(r[1]['extra_body']['metadata']['session_id'] == provider_session for r in provider.requests)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_structured_submit_result_schema_and_terminal(tmp_path):
    schema = {'type': 'object', 'properties': {'amount': {'type': 'integer'}}, 'required': ['amount'], 'additionalProperties': False}
    store, _, provider, driver, session, calls = setup(tmp_path,
        [reply(call('bad', 'submit_result', {'amount': 'x'})), reply(call('ok', 'submit_result', {'amount': 7}))],
        policy=LLMPolicy(result_schema=schema))
    try:
        result = await driver.run(session)
        assert result.status == 'completed' and result.value['result'] == {'amount': 7}
        assert len(provider.requests) == 2 and not calls
    finally:
        store.close()


def sdk_response(text='done'):
    from openai.types.chat import ChatCompletion
    return ChatCompletion(id='response', created=0, model='fake', object='chat.completion',
                          choices=[{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': text}}])


def real_provider(tmp_path, **options):
    store = StageStore.create(tmp_path / 'run', THREAD_SCHEMA)
    threads = ThreadStore(store)
    tid = threads.open('a', Moment(1, 'p'), 'decision')
    threads.append_message(tid, {'role': 'user', 'content': 'complete original'})
    provider = ModelProvider([{'id': 'test', 'api_key': 'unused-test-value', 'base_url': 'http://unused.invalid/v1',
                               'model': 'fake', 'concurrency': 2, 'trust_env': False}], threads,
                             retry_delay=0, **options)
    return store, threads, tid, provider


@pytest.mark.asyncio
async def test_physical_retry_preserves_request_reference_and_full_raw_response(tmp_path):
    import httpx
    import openai
    store, threads, tid, provider = real_provider(tmp_path)
    requests = []
    async def create(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            raise openai.APITimeoutError(request=httpx.Request('POST', 'http://unused.invalid'))
        return sdk_response('complete response')
    provider.manager.clients['test'].chat.completions.create = create
    try:
        assert provider.manager.clients['test'].max_retries == 0
        result = await provider.request(tid, {'temperature': 0.7, 'extra_body': {'metadata': {'session_id': 'stable'}}})
        assert result['content'] == 'complete response'
        assert len(requests) == 2 and requests[0]['messages'] is requests[1]['messages']
        events = threads.tail(tid)['items']
        recorded = [e for e in events if e['kind'] == 'request']
        assert len(recorded) == 2
        rebuilt = threads.read_request(tid, recorded[1]['seq'])
        assert rebuilt['messages'] == [{'role': 'user', 'content': 'complete original'}]
        assert rebuilt['retry_of'] == recorded[0]['seq']
        assert rebuilt['provider_options']['model'] == 'fake'
        assert rebuilt['provider_options']['temperature'] == 0.7
        response = [e for e in events if e['kind'] == 'provider_response'][0]
        assert response['payload']['payload']['raw_response']['choices'][0]['message']['content'] == 'complete response'
    finally:
        await provider.close()
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['request', 'response'])
async def test_required_thread_failure_never_retries_actual_model_request(tmp_path, failure):
    from society0.kernel.models import ThreadWriteError
    store, threads, tid, provider = real_provider(tmp_path, max_attempts=3)
    calls = []
    async def create(**kwargs):
        calls.append(True)
        return sdk_response()
    provider.manager.clients['test'].chat.completions.create = create
    def broken(*args, **kwargs):
        raise OSError('disk full')
    if failure == 'request':
        threads.record_request = broken
    else:
        threads.event = broken
    try:
        with pytest.raises(ThreadWriteError):
            await provider.request(tid, {})
        assert len(calls) == (0 if failure == 'request' else 1)
    finally:
        await provider.close()
        store.close()


@pytest.mark.asyncio
async def test_empty_retry_then_exhaustion_without_domain_calls(tmp_path):
    store, _, provider, driver, session, calls = setup(tmp_path, [reply(), reply()], policy=LLMPolicy(empty_retries=1))
    try:
        result = await driver.run(session)
        assert result.status == 'incomplete' and result.reason == 'empty_response'
        assert len(provider.requests) == 2 and not calls
    finally:
        store.close()


@pytest.mark.asyncio
async def test_action_selection_and_live_rejection_are_feedback_not_domain_fault(tmp_path):
    store, _, provider, driver, session, calls = setup(tmp_path, [reply(invoke()), reply(text='done')],
                                                    policy=LLMPolicy(allowed_names=()))
    try:
        assert (await driver.run(session)).status == 'completed'
        assert not calls
    finally:
        store.close()


@pytest.mark.asyncio
async def test_domain_unavailable_between_discovery_and_invoke_is_feedback(tmp_path):
    store, _, provider, driver, session, calls = setup(tmp_path, [reply(invoke()), reply(text='done')])
    session.actions.actions._allows = lambda *a: False
    try:
        assert (await driver.run(session)).status == 'completed'
        assert len(provider.requests) == 2 and not calls
    finally:
        store.close()


@pytest.mark.asyncio
async def test_completed_terminal_batch_records_unexecuted_siblings_for_continuation(tmp_path):
    store, threads, provider, driver, session, calls = setup(tmp_path, [reply(invoke('first'), invoke('second'))],
        terminal=True, policy=LLMPolicy(parallel_tool_calls=True))
    try:
        assert (await driver.run(session)).status == 'completed'
        messages = threads.read_messages(session.cursors['thread_id'])
        feedback = {m['tool_call_id']: m['content'] for m in messages if m['role'] == 'tool'}
        assert set(feedback) == {'first', 'second'}
        assert 'not_executed' in feedback['second']
        assert len(calls) == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_per_action_batch_limit_and_tag_completion(tmp_path):
    store, _, provider, driver, session, calls = setup(tmp_path, [reply(invoke('a'), invoke('b'))],
        policy=LLMPolicy(parallel_tool_calls=True, per_action_limits={'work': 1}))
    try:
        assert (await driver.run(session)).reason == 'per_action_budget_exhausted'
        assert not calls
    finally:
        store.close()
    other = tmp_path / 'tags'
    other.mkdir()
    store, _, provider, driver, session, calls = setup(other, [reply(invoke())], tags=('finished',),
        policy=LLMPolicy(required_tags=('finished',), completion_tags=('finished',)))
    try:
        assert (await driver.run(session)).status == 'completed'
        assert len(calls) == len(provider.requests) == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_exact_selection_does_not_expand_same_named_tag(tmp_path):
    find = call('find', 'action_find', {'target': {'namespace': 'm', 'kind': 'job', 'key': '1'}, 'query': '', 'limit': 100, 'cursor': None})
    store, threads, provider, driver, session, calls = setup(tmp_path, [reply(find), reply(text='done')],
        tags=('unrelated',), policy=LLMPolicy(allowed_names=('work',)))
    session.actions.actions.register(Action('other', ('m', 'job'), '', {}, lambda *a: ActionResult('completed'), tags=('work',)))
    try:
        await driver.run(session)
        message = [m for m in threads.read_messages(session.cursors['thread_id']) if m['role'] == 'tool'][0]
        result = json.loads(message['content'])
        assert result['total'] == 1 and result['items'][0]['name'] == 'work'
    finally:
        store.close()


@pytest.mark.asyncio
async def test_long_thread_never_projects_a_bounded_message_window(tmp_path):
    responses = [reply(invoke(str(i))) for i in range(25)] + [reply(text='done')]
    store, threads, provider, driver, session, calls = setup(tmp_path, responses)
    try:
        assert (await driver.run(session)).status == 'completed'
        assert len(calls) == 25
        last_messages = provider.requests[-1][2]
        assert len(last_messages) == 52
        assert last_messages[0]['content'] == 'persona precision reminders 完整内容'
        assert all(str(i) in [m.get('tool_call_id') for m in last_messages] for i in range(25))
    finally:
        store.close()


@pytest.mark.asyncio
async def test_context_failure_is_incomplete_and_strict_request_is_not_downgraded(tmp_path):
    from society0.kernel.models import ProviderFailure
    store, _, provider, driver, session, calls = setup(tmp_path,
        [ProviderFailure('context_limit', 'full context too long')], policy=LLMPolicy(strict_tools=True))
    try:
        assert (await driver.run(session)).reason == 'context_limit'
        assert not calls
        assert all(t['function']['strict'] for t in provider.requests[0][1]['tools'])
        assert all(t['function']['parameters']['additionalProperties'] is False for t in provider.requests[0][1]['tools'])
    finally:
        store.close()


@pytest.mark.asyncio
async def test_direct_json_measurement_has_no_domain_tools(tmp_path):
    store, _, provider, driver, session, calls = setup(tmp_path, [reply(text='{"score":3}')],
        policy=LLMPolicy(direct_json=True, result_schema={'type': 'object', 'properties': {'score': {'type': 'integer'}}, 'required': ['score']}))
    try:
        result = await driver.run(session)
        assert result.value['result'] == {'score': 3} and not calls
        assert 'tools' not in provider.requests[0][1]
        assert provider.requests[0][1]['response_format']['json_schema']['strict']
    finally:
        store.close()


@pytest.mark.asyncio
async def test_shell_actual_domain_attempts_share_budget_and_no_reexecution(tmp_path):
    from society0.kernel.shell import ShellSession
    def command():
        import shlex
        return 'action invoke ' + shlex.quote(json.dumps({'name': 'work', 'target': {'namespace': 'm', 'kind': 'job', 'key': '1'}, 'arguments': {}}))
    response = reply(call('bash-1', 'bash', {'script': command() + '; ' + command()}))
    store, threads, provider, driver, session, calls = setup(tmp_path, [response], policy=LLMPolicy(max_action_calls=1))
    driver.shell_factory = lambda s, ledger: ShellSession(s.scope, s.information.information,
        bound_actions=ledger, result_dir=tmp_path / 'results')
    try:
        result = await driver.run(session)
        assert result.status == 'incomplete' and result.reason == 'action_budget_exhausted'
        assert len(calls) == len(provider.requests) == 1
        assert calls[0][1] == 'bash-1:1'
    finally:
        store.close()


@pytest.mark.asyncio
async def test_terminal_inside_shell_stops_later_domain_calls(tmp_path):
    from society0.kernel.shell import ShellSession
    import shlex
    command = 'action invoke ' + shlex.quote(json.dumps({'name': 'work', 'target': {'namespace': 'm', 'kind': 'job', 'key': '1'}, 'arguments': {}}))
    store, _, provider, driver, session, calls = setup(tmp_path,
        [reply(call('shell', 'bash', {'script': command + '; ' + command}))], terminal=True)
    driver.shell_factory = lambda s, ledger: ShellSession(s.scope, s.information.information,
        bound_actions=ledger, result_dir=tmp_path / 'results')
    try:
        assert (await driver.run(session)).status == 'completed'
        assert len(calls) == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_filtered_discovery_cursor_binds_subject_and_moment(tmp_path):
    from society0.kernel.llm import _Ledger
    store, threads, _, driver, session, _ = setup(tmp_path, [])
    session.actions.actions.register(Action('other', ('m', 'job'), '', {}, lambda *a: ActionResult('completed')))
    tid = threads.open('a', session.moment, 'decision')
    first = _Ledger(driver, session, tid)
    target = Ref('m', 'job', '1')
    try:
        cursor = (await first.find(target, limit=1)).next_cursor
        other = Session(Actor('b', driver), InteractionScope('b', session.moment), session.information,
                        session.actions, {}, None, (), None)
        with pytest.raises(ValueError, match='cursor'):
            await _Ledger(driver, other, tid).find(target, limit=1, cursor=cursor)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_malformed_inner_arguments_consume_failed_domain_attempt(tmp_path):
    malformed = call('bad', 'action_invoke', {'name': 'work', 'target': {'namespace': 'm', 'kind': 'job', 'key': '1'}, 'arguments': '{not json'})
    store, _, provider, driver, session, calls = setup(tmp_path, [reply(malformed)], policy=LLMPolicy(max_action_calls=1))
    try:
        assert (await driver.run(session)).reason == 'action_budget_exhausted'
        assert not calls and len(provider.requests) == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_interview_tools_are_measurement_only(tmp_path):
    schema = {'type': 'object', 'properties': {'score': {'type': 'integer'}}, 'required': ['score']}
    store, _, provider, driver, session, calls = setup(tmp_path,
        [reply(call('result', 'submit_result', {'score': 2}))], policy=LLMPolicy(mode='interview', result_schema=schema))
    try:
        assert (await driver.run(session)).status == 'completed'
        assert [t['function']['name'] for t in provider.requests[0][1]['tools']] == ['submit_result']
        assert not calls
    finally:
        store.close()


@pytest.mark.asyncio
async def test_default_data_read_is_utf8_and_invalid_query_is_tool_feedback(tmp_path):
    from society0.kernel.interaction import DocumentChunk
    class Document:
        def ref(self, path): return Ref('m', 'doc', path)
        def read(self, scope, path, *, offset, size):
            body = '完整正文🙂'.encode()
            return DocumentChunk(body[offset:offset+size], len(body), None, 1, self.ref(path))
    read = call('read', 'data_read', {'path': '/doc', 'offset': 0, 'size': 100})
    invalid = call('query', 'data_query', {'path': '/doc', 'query': '{bad'})
    store, threads, provider, driver, session, calls = setup(tmp_path, [reply(read), reply(invalid), reply(text='done')])
    session.information.information.mount('/doc', Document())
    try:
        assert (await driver.run(session)).status == 'completed'
        tools = [json.loads(m['content']) for m in threads.read_messages(session.cursors['thread_id']) if m['role'] == 'tool']
        assert tools[0]['data'] == '完整正文🙂' and tools[0]['encoding'] == 'utf-8'
        assert 'error' in tools[1]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_restore_same_moment_reuses_thread_and_prior_tool_receipt(tmp_path):
    store, threads, provider, driver, session, calls = setup(tmp_path, [reply(invoke('stable')), reply(text='done')])
    await driver.run(session)
    tid = session.cursors['thread_id']
    provider_session = threads.describe(tid)['provider_session_id']
    store.complete(1)
    store.close()
    restored = StageStore.restore(tmp_path / 'run', tmp_path / 'branch', step=1)
    try:
        recovered_threads = ThreadStore(restored)
        recovered_provider = FakeProvider(recovered_threads, [reply(invoke('stable')), reply(text='again')])
        recovered_driver = LLMDriver(recovered_provider, recovered_threads, input_builder=lambda s: [{'role': 'user', 'content': 'resume'}])
        recovered_scope = InteractionScope('a', Moment(1, 'p'))
        recovered_session = Session(Actor('a', recovered_driver), recovered_scope, session.information,
                                    session.actions, {}, None, (), None)
        assert (await recovered_driver.run(recovered_session)).status == 'completed'
        assert recovered_session.cursors['thread_id'] == tid
        assert recovered_threads.describe(tid)['provider_session_id'] == provider_session
        assert len(calls) == 1
    finally:
        restored.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("via_shell", [False, True])
async def test_shell_output_artifact_survives_restore_and_result_read(tmp_path, via_shell):
    from society0.kernel.shell import ShellSession
    from pathlib import Path
    store, threads, provider, driver, session, calls = setup(tmp_path,
        [reply(call('script', 'bash', {'script': "printf '原文🙂'"})), reply(text='done')])
    phase = SimpleNamespace(prepare_artifact=store.prepare_artifact)
    session = Session(session.actor, session.scope, session.information, session.actions, session.cursors, None, (), phase)
    driver.shell_factory = lambda s, ledger: ShellSession(s.scope, s.information.information,
        bound_actions=ledger, result_dir=tmp_path / 'results')
    try:
        await driver.run(session)
        tid = session.cursors['thread_id']
        result = json.loads([m for m in threads.read_messages(tid) if m['role'] == 'tool'][0]['content'])
        reference = result['stdout_ref']
        store.complete(1)
    finally:
        store.close()
    import shutil
    shutil.rmtree(tmp_path / 'results')
    restored = StageStore.restore(tmp_path / 'run', tmp_path / 'branch', step=1)
    try:
        recovered_threads = ThreadStore(restored)
        read_call = call('read', 'bash', {'script': f'result read {reference}'}) if via_shell else call('read', 'result_read', {'reference': reference, 'offset': 0, 'size': 100})
        recovered_provider = FakeProvider(recovered_threads, [reply(read_call), reply(text='done')])
        recovered_driver = LLMDriver(recovered_provider, recovered_threads, input_builder=lambda s: [])
        recovered = Session(Actor('a', recovered_driver), InteractionScope('a', Moment(1, 'p')), session.information,
                            session.actions, {}, None, (), SimpleNamespace(prepare_artifact=restored.prepare_artifact))
        if via_shell:
            recovered_driver.shell_factory = lambda s, ledger: ShellSession(s.scope, s.information.information,
                bound_actions=ledger, result_dir=tmp_path / 'new-results',
                result_reader=lambda reference, **options: recovered_driver.read_result(s, reference, **options))
        await recovered_driver.run(recovered)
        feedback = json.loads([m for m in recovered_threads.read_messages(tid) if m.get('tool_call_id') == 'read'][0]['content'])
        if via_shell:
            feedback = json.loads(feedback['stdout'])
        assert feedback['data'] == '原文🙂' and feedback['next_offset'] is None
    finally:
        restored.close()


@pytest.mark.asyncio
async def test_fact_union_keeps_full_read_text_and_changed_write_resets_only_coverage(tmp_path):
    def named(identifier, name):
        return invoke(identifier, name=name)
    responses = [reply(named('r1', 'read1')), reply(named('r2', 'read2')),
                 reply(named('n', 'noop')), reply(named('r3', 'read1')),
                 reply(named('w', 'write')), reply(named('r4', 'read1')), reply(text='done')]
    store, threads, provider, driver, session, calls = setup(tmp_path, responses)
    fact1, fact2 = {'namespace': 'm', 'kind': 'fact', 'key': '1'}, {'namespace': 'm', 'kind': 'fact', 'key': '2'}
    for name, facts in [('read1', [fact1]), ('read2', [fact1, fact2])]:
        session.actions.actions.register(Action(name, ('m', 'job'), '', {},
            lambda *a, facts=facts: ActionResult('completed', {'text': '完整原文不能删', 'facts': facts}), read_only=True))
    for name, changed in [('noop', False), ('write', True)]:
        session.actions.actions.register(Action(name, ('m', 'job'), '', {},
            lambda *a, changed=changed: ActionResult('completed', {'changed': changed})))
    try:
        assert (await driver.run(session)).status == 'completed'
        feedback = {m['tool_call_id']: json.loads(m['content']) for m in threads.read_messages(session.cursors['thread_id']) if m['role'] == 'tool'}
        assert feedback['r2']['observation']['known_facts'] == [fact1, fact2]
        assert feedback['r3']['observation']['repeated_facts'] == [fact1]
        assert feedback['r4']['observation']['repeated_facts'] == []
        for name in ('r1', 'r2', 'r3', 'r4'):
            assert feedback[name]['result']['value']['text'] == '完整原文不能删'
    finally:
        store.close()


@pytest.mark.asyncio
async def test_two_concurrent_actors_have_task_local_action_call_ids(tmp_path):
    import asyncio
    entered = asyncio.Event()
    seen = []
    async def work(scope, target, arguments):
        first = current_action_call_id.get()
        if scope.actor == 'a':
            entered.set()
            await asyncio.sleep(0)
        else:
            await entered.wait()
        seen.append((scope.actor, first, current_action_call_id.get()))
        return ActionResult('completed')
    store, threads, provider, driver, session, calls = setup(tmp_path, [reply(invoke('a-call')), reply(invoke('b-call'))], terminal=True, handler=work)
    other_scope = InteractionScope('b', session.moment)
    other = Session(Actor('b', driver), other_scope, session.information.information.bound(other_scope),
                    session.actions.actions.bound(other_scope), {}, None, (), None)
    try:
        results = await asyncio.gather(driver.run(session), driver.run(other))
        assert all(result.status == 'completed' for result in results)
        assert sorted(seen) == [('a', 'a-call', 'a-call'), ('b', 'b-call', 'b-call')]
        assert current_action_call_id.get() is None
    finally:
        store.close()


@pytest.mark.asyncio
async def test_cancelled_provider_keeps_thread_incomplete_and_no_actions(tmp_path):
    started, released = asyncio.Event(), asyncio.Event()
    store, threads, provider, driver, session, calls = setup(tmp_path, [])
    async def request(thread_id, options):
        threads.record_request(thread_id, provider_options=options, physical_request_id='cancelled')
        started.set()
        try:
            await asyncio.Future()
        finally:
            released.set()
    provider.request = request
    try:
        task = asyncio.create_task(driver.run(session))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert released.is_set() and not calls
        assert threads.describe(session.cursors['thread_id'])['status'] == 'incomplete'
        with pytest.raises(RuntimeError, match='recovery'):
            await driver.run(session)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_memory_hooks_keep_original_input_boundary_and_thread_open(tmp_path):
    store,threads,provider,driver,session,calls=setup(tmp_path,[reply(text='decision')])
    seen=[]
    async def before(current,tid):
        seen.append(('before',threads.describe(tid)['status']))
        return [{'role':'user','content':'full recalled memory'}]
    async def after(current,tid,result):
        seen.append(('after',threads.describe(tid)['status']))
        assert result.value['memory_input_through']==threads.describe(tid)['last_seq']
        threads.append_message(tid,{'role':'user','content':'extract memory from complete decision'})
        assert result.value['memory_input_through']<threads.describe(tid)['last_seq']
    driver.memory=SimpleNamespace(before_activation=before,after_activation=after)
    try:
        result=await driver.run(session)
        assert result.status=='completed'
        assert seen==[('before','open'),('after','open')]
        assert provider.requests[0][2][0]['role']=='system'
        assert provider.requests[0][2][-1]['content']=='full recalled memory'
    finally:store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',[False,True])
async def test_memory_success_hook_skips_incomplete_and_failure_marks_thread_incomplete(tmp_path,failure):
    store,threads,provider,driver,session,calls=setup(tmp_path,[reply(text='decision',finish='stop' if failure else 'length')])
    seen=[]
    async def before(*args):return []
    async def after(*args):
        seen.append(True)
        raise OSError('embedding evidence failed')
    driver.memory=SimpleNamespace(before_activation=before,after_activation=after)
    try:
        if failure:
            with pytest.raises(OSError,match='embedding evidence'):await driver.run(session)
        else:assert (await driver.run(session)).status=='incomplete'
        assert seen==([True] if failure else [])
        assert threads.describe(session.cursors['thread_id'])['status']=='incomplete'
    finally:store.close()
