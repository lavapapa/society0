"""低层 Model/EmbeddingModel 适配：规范留证、资源许可与明确的重试拥有者。"""
from __future__ import annotations
import asyncio
from collections import OrderedDict, deque
from contextlib import AsyncExitStack
from dataclasses import dataclass
from functools import wraps
import json
import random
import time
import traceback
import uuid

import httpx2
from openai import APIConnectionError, APIResponseValidationError
from openai.resources.embeddings import AsyncEmbeddings
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.output import OutputObjectDefinition
from pydantic_ai.tools import ToolDefinition

from ..resource_managers import RequestResources, redact_credentials
from . import usage
from .model_messages import encode, display, display_response, history


class ThreadModel(Model):
    """Agent 使用单一物理提供方入口；原始 typed 响应原样返回。"""
    def __init__(self, request):
        super().__init__()
        self._request = request

    @property
    def model_name(self): return 'society0-thread'

    @property
    def system(self): return 'society0'

    async def request(self, messages, model_settings, model_request_parameters):
        return await self._request(messages)


class ProviderFailure(RuntimeError):
    def __init__(self, reason, message):
        self.reason = reason
        super().__init__(message)


class ThreadWriteError(RuntimeError): pass


class _OrderedEmbeddings(AsyncEmbeddings):
    async def create(self, **kwargs):
        response = await super().create(**kwargs)
        # Pydantic AI 2.54.0 与上游 main 的 embeddings/openai.py 丢弃 index；
        # 在公开 SDK 资源边界恢复顺序，仍由 SDK 负责 HTTP、解码和用量。
        response.data.sort(key=lambda item: item.index)
        if [item.index for item in response.data] != list(range(len(kwargs['input']))):
            request = httpx2.Request('POST', str(self._client.base_url) + 'embeddings')
            raise APIResponseValidationError(httpx2.Response(200, request=request),
                response.model_dump(mode='json'), message='embedding response indices differ from request')
        return response


def _operation(method):
    @wraps(method)
    async def scoped(self, *args, **kwargs):
        await self._start()
        result = asyncio.get_running_loop().create_future()
        async def invoke():
            try: value = await method(self, *args, **kwargs)
            except asyncio.CancelledError: result.cancel()
            except BaseException as error:
                if not result.done(): result.set_exception(error)
            else:
                if not result.done(): result.set_result(value)
        task = self._group.create_task(invoke())
        try: return await result
        except asyncio.CancelledError:
            if not task.cancelling(): task.cancel()
            async def drain():
                try: await asyncio.wait((task,))
                except asyncio.CancelledError: await asyncio.wait((task,))
            async with asyncio.TaskGroup() as group: group.create_task(drain())
            raise
    return scoped


class _Provider:
    def __init__(self, endpoints, *, max_attempts=2, retry_delay=.1, request_limit=None,
                 global_concurrency=None, http_connections=None, request_jitter=0, embedding=False):
        if type(max_attempts) is not int or max_attempts < 1: raise ValueError('max_attempts must be positive')
        if retry_delay < 0: raise ValueError('retry_delay must be nonnegative')
        if global_concurrency is not None and (type(global_concurrency) is not int or global_concurrency < 1):
            raise ValueError('global_concurrency must be positive')
        if http_connections is not None and (type(http_connections) is not int or http_connections < 1):
            raise ValueError('http_connections must be positive')
        self._closed = False
        self._owner = self._group = None
        self._ready, self._stopped = asyncio.Event(), asyncio.Event()
        self._startup_error = None
        self.max_attempts, self.retry_delay = max_attempts, retry_delay
        self._profile_limit = asyncio.Semaphore(global_concurrency) if global_concurrency is not None else None
        self.endpoints = [_Endpoint(item, request_limit, http_connections, request_jitter, embedding) for item in endpoints]
        if not self.endpoints: raise ValueError('at least one endpoint is required')
        if len({endpoint.model_name for endpoint in self.endpoints}) != 1:
            raise ValueError('one profile must use one model; select other profiles explicitly')

    async def _start(self):
        if self._closed: raise RuntimeError('provider is closed')
        if self._owner is None:
            self._owner = asyncio.create_task(self._serve())
            def finished(task):
                self._closed = True
                self._ready.set()
                self._stopped.set()
            self._owner.add_done_callback(finished)
        await self._ready.wait()
        if self._startup_error is not None: raise self._startup_error
        if self._closed: raise RuntimeError('provider is closed')

    async def _serve(self):
        try:
            async with AsyncExitStack() as stack:
                async def close_endpoint(endpoint):
                    try: await endpoint.close()
                    except asyncio.CancelledError as error:
                        raise RuntimeError('provider resource cleanup was cancelled') from error
                for endpoint in self.endpoints:
                    stack.push_async_callback(close_endpoint, endpoint)
                    await endpoint.start()
                async with asyncio.TaskGroup() as group:
                    self._group = group
                    self._ready.set()
                    await asyncio.Future()
        except asyncio.CancelledError: pass
        except BaseException as error: self._startup_error = error
        finally:
            self._closed = True
            self._ready.set()
            self._stopped.set()

    def _select(self):
        return random.choices(self.endpoints, weights=[endpoint.weight for endpoint in self.endpoints], k=1)[0]

    async def close(self):
        first = not self._closed
        self._closed = True
        if self._owner is None: return
        if first: self._owner.cancel()
        async def finish():
            try: await self._stopped.wait()
            except asyncio.CancelledError: await self._stopped.wait()
        async with asyncio.TaskGroup() as group: group.create_task(finish())
        if self._startup_error is not None: raise self._startup_error


class _Endpoint:
    def __init__(self, config, shared, connections, jitter, embedding):
        self.id, self.model_name = config['id'], config['model']
        self.kind = config.get('provider_type', 'openai')
        self.weight = config.get('weight', 1)
        if self.weight <= 0: raise ValueError('endpoint weight must be positive')
        self.timeout = config.get('timeout', 30)
        self.tool_choice_policy = config.get('tool_choice_policy', 'native')
        if self.tool_choice_policy not in ('native', 'auto_restrict'): raise ValueError('unknown tool choice policy')
        self.send_dimensions = config.get('send_dimensions', True)
        self.capacity = config.get('concurrency', 1)
        self.resources = RequestResources(self.capacity, shared=shared,
                                          rpm=config.get('rpm'), jitter=jitter)
        self.secret = config.get('api_key')
        self.http = None
        self.client = None
        self.auth = None
        self.config, self.connections, self.embedding = dict(config), connections, embedding
        if self.kind == 'google':
            from pydantic_ai.models.google import GoogleModelSettings
            self.settings_type = GoogleModelSettings
        elif self.kind == 'anthropic':
            from pydantic_ai.models.anthropic import AnthropicModelSettings
            self.settings_type = AnthropicModelSettings
        elif self.kind in ('openai', 'openai-responses', 'azure', 'ollama', 'siwc'):
            from pydantic_ai.models.openai import OpenAIChatModelSettings, OpenAIResponsesModelSettings
            self.settings_type = OpenAIResponsesModelSettings if self.kind in ('siwc', 'openai-responses') else OpenAIChatModelSettings
        else: raise ValueError('unsupported provider_type: ' + self.kind)
        if embedding and self.kind in ('anthropic', 'siwc'):
            raise ValueError('selected provider does not provide embeddings')

    async def start(self):
        config, connections, embedding = self.config, self.connections, self.embedding
        if self.kind == 'google':
            from google.genai import Client, types
            from pydantic_ai.providers.google import GoogleProvider
            from pydantic_ai.models.google import GoogleModel, GoogleModelSettings
            from pydantic_ai.embeddings.google import GoogleEmbeddingModel
            self.client = Client(api_key=self.secret, http_options=types.HttpOptions(
                timeout=int(self.timeout * 1000), retry_options=types.HttpRetryOptions(attempts=1)))
            provider = GoogleProvider(client=self.client)
            self.model = (GoogleEmbeddingModel if embedding else GoogleModel)(self.model_name, provider=provider)
            self.settings_type = GoogleModelSettings
        else:
            self.http = httpx2.AsyncClient(timeout=self.timeout, trust_env=config.get('trust_env', True),
                limits=httpx2.Limits(max_connections=connections or config.get('concurrency', 1),
                                    max_keepalive_connections=connections or config.get('concurrency', 1)))
            if self.kind == 'anthropic':
                if embedding: raise ValueError('Anthropic does not provide this embedding contract')
                from anthropic import AsyncAnthropic
                from pydantic_ai.providers.anthropic import AnthropicProvider
                from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
                self.client = AsyncAnthropic(api_key=self.secret, base_url=config.get('base_url'),
                                             http_client=self.http, max_retries=0)
                self.model = AnthropicModel(self.model_name, provider=AnthropicProvider(anthropic_client=self.client))
                self.settings_type = AnthropicModelSettings
            elif self.kind in ('openai', 'openai-responses', 'azure', 'ollama', 'siwc'):
                from openai import AsyncOpenAI, AsyncAzureOpenAI
                from pydantic_ai.providers.openai import OpenAIProvider
                from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel, OpenAIChatModelSettings, OpenAIResponsesModelSettings
                from pydantic_ai.embeddings.openai import OpenAIEmbeddingModel
                extra = {}
                if self.kind == 'siwc':
                    if embedding: raise ValueError('ChatGPT subscription does not provide embeddings')
                    from .auth import ChatGPTAuth, RESOURCE
                    from pydantic_ai.providers.openai_codex import OpenAICodexProvider
                    self.auth = ChatGPTAuth(config.get('account', 'default'), directory=config.get('credentials_directory'))
                    api_key, base_url = 'authentication-pending', RESOURCE
                    profile = dict(OpenAICodexProvider.model_profile(self.model_name))
                    profile['openai_system_prompt_role'] = 'developer'
                    extra['profile'] = profile
                else: api_key, base_url = self.secret, config.get('base_url')
                if self.kind == 'azure':
                    self.client = AsyncAzureOpenAI(api_key=api_key, azure_endpoint=base_url,
                        azure_deployment=config.get('deployment_name'), api_version=config['api_version'],
                        http_client=self.http, max_retries=0)
                else:
                    self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, http_client=self.http, max_retries=0)
                provider = OpenAIProvider(openai_client=self.client)
                if embedding:
                    self.client.embeddings = _OrderedEmbeddings(self.client)
                    self.model = OpenAIEmbeddingModel(self.model_name, provider=provider)
                else:
                    model_class = OpenAIResponsesModel if self.kind in ('siwc', 'openai-responses') else OpenAIChatModel
                    if model_class is OpenAIChatModel:
                        extra['profile'] = {'openai_chat_streaming_requires_finish_reason': True}
                    self.model = model_class(self.model_name, provider=provider, **extra)
                    self.settings_type = OpenAIResponsesModelSettings if model_class is OpenAIResponsesModel else OpenAIChatModelSettings
            else: raise ValueError('unsupported provider_type: ' + self.kind)

    async def close(self):
        if self.kind == 'google' and self.client is not None:
            try: await self.client.aio.aclose()
            finally: self.client.close()
        elif self.client is not None: await self.client.close()
        elif self.http is not None: await self.http.aclose()


def _parameters(endpoint, options):
    settings = dict(options)
    tools = settings.pop('tools', [])
    definitions = [ToolDefinition(name=tool['function']['name'], description=tool['function'].get('description'),
        parameters_json_schema=tool['function']['parameters'], strict=tool['function'].get('strict')) for tool in tools]
    choice = settings.get('tool_choice', 'auto')
    named = choice.get('function', {}).get('name') if isinstance(choice, dict) else None
    resolution = None
    if named is not None:
        if sum(tool.name == named for tool in definitions) != 1: raise ValueError('named tool is not declared exactly once')
        settings['tool_choice'] = [named]
    if endpoint.tool_choice_policy == 'auto_restrict' and (named is not None or choice == 'required'):
        if named is not None: definitions = [tool for tool in definitions if tool.name == named]
        settings['tool_choice'] = 'auto'
        resolution = {'policy': 'auto_restrict', 'requested': choice, 'effective': 'auto',
            'selected_tool_name': named, 'tools_filtered': len(definitions) != len(tools),
            'original_tools_count': len(tools), 'effective_tools_count': len(definitions)}
    params = ModelRequestParameters(function_tools=definitions)
    output = settings.pop('response_format', None)
    if output is not None:
        if output['type'] != 'json_schema': raise ValueError('structured output requires json_schema')
        schema = output['json_schema']
        params.output_mode = 'native'
        params.output_object = OutputObjectDefinition(schema['schema'], name=schema.get('name'), strict=schema.get('strict'))
    if endpoint.kind == 'siwc':
        prohibited = {'temperature', 'top_p', 'max_tokens', 'max_output_tokens', 'metadata', 'previous_response_id',
                      'presence_penalty', 'frequency_penalty', 'logprobs', 'seed', 'openai_previous_response_id'}
        invalid = prohibited.intersection(settings) | prohibited.intersection(settings.get('extra_body') or {})
        if invalid or output is not None:
            raise ValueError('SIWC profile cannot satisfy these parameters: ' + ', '.join(sorted(invalid or {'response_format'})))
        extra = dict(settings.get('extra_body') or {})
        if any(key in extra for key in ('tools', 'input', 'stream', 'store')):
            raise ValueError('SIWC wire tools/input/stream/store are owned by the adapter')
        extra['tools'] = [{'type': 'namespace', 'name': 'society', 'description': 'Society0 bound tools',
            'tools': [{'type': 'function', 'name': tool.name, 'description': tool.description or '',
                       'parameters': tool.parameters_json_schema, **({'strict': tool.strict} if tool.strict is not None else {})}
                      for tool in definitions]}] if definitions else []
        if named is not None and endpoint.tool_choice_policy == 'native':
            extra['tool_choice'] = {'type': 'function', 'namespace': 'society', 'name': named}
        settings['extra_body'] = extra
        settings['openai_store'] = False
    if 'openai_unsupported_model_settings' in settings:
        raise ValueError('silently dropping model settings is unsupported')
    unknown = set(settings) - set(endpoint.settings_type.__annotations__)
    if unknown: raise ValueError('unsupported model settings: ' + ', '.join(sorted(unknown)))
    if endpoint.kind == 'google' and 'extra_body' in settings:
        raise ValueError('Google native settings use google_* fields, not OpenAI extra_body')
    return settings, params, resolution


def _usage(result):
    # SDK 的未报告计数是类默认值；仅实例实际赋值表示提供方报告。
    present = vars(result.usage)
    counts = {}
    for source, target in (('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens'),
                           ('cache_read_tokens', 'cache_read_tokens'), ('cache_write_tokens', 'cache_write_tokens')):
        if source in present: counts[target] = present[source]
    if 'input_tokens' in present and 'output_tokens' in present:
        counts['total_tokens'] = present['input_tokens'] + present['output_tokens']
    else:
        from pydantic_ai.embeddings import EmbeddingResult
        if isinstance(result, EmbeddingResult) and 'input_tokens' in present:
            counts['total_tokens'] = result.usage.total_tokens
    return counts


def _transient(error, kind):
    if kind == 'google':
        import httpx
        if isinstance(error, httpx.TransportError): return True
    status = getattr(error, 'status_code', None)
    return (isinstance(error, (TimeoutError, httpx2.TransportError, APIConnectionError))
            or status in (408, 409, 429) or (isinstance(status, int) and status >= 500)
            or (error.__cause__ is not None and _transient(error.__cause__, kind)))


class ModelProvider(_Provider):
    def __init__(self, endpoints, threads, *, request_options=None, session_transport=None,
                 max_attempts=2, retry_delay=.1, global_concurrency=None, http_connections=None,
                 request_jitter=0, request_limit=None):
        super().__init__(endpoints, max_attempts=max_attempts, retry_delay=retry_delay,
            global_concurrency=global_concurrency, http_connections=http_connections,
            request_jitter=request_jitter, request_limit=request_limit)
        if session_transport not in (None, 'metadata'): raise ValueError('unsupported session transport')
        if session_transport and any(endpoint.kind == 'siwc' for endpoint in self.endpoints):
            raise ValueError('SIWC does not accept session metadata')
        self.threads, self.request_options, self.session_transport = threads, dict(request_options or {}), session_transport
        for endpoint in self.endpoints: _parameters(endpoint, self.request_options)

    def validate_options(self, options):
        for endpoint in self.endpoints: _parameters(endpoint, {**self.request_options, **options})

    async def request(self, thread_id, options):
        response, sequence, incomplete = await self.request_model(thread_id, options)
        semantic = display_response(response)
        semantic.update(finish_reason=response.finish_reason, message_seq=sequence)
        if incomplete: semantic['incomplete_reason'] = incomplete
        return semantic

    @_operation
    async def request_model(self, thread_id, options, *, model_messages=None):
        endpoint = self._select()
        selected = {**self.request_options, **options}
        head = self.threads.describe(thread_id)
        if self.session_transport:
            selected['extra_body'] = {**selected.get('extra_body', {}), 'metadata': {
                **selected.get('extra_body', {}).get('metadata', {}), 'session_id': head['provider_session_id']}}
        settings, params, resolution = _parameters(endpoint, selected)
        through, retry_of = head['last_seq'], None
        for attempt in range(self.max_attempts):
            messages = result = partial = None
            physical = None
            started = time.perf_counter()
            timing = {}
            try:
                async with AsyncExitStack() as stack:
                    if self._profile_limit is not None: await stack.enter_async_context(self._profile_limit)
                    profile_wait = time.perf_counter() - started
                    timing = await stack.enter_async_context(endpoint.resources.acquire())
                    timing['queue_s'] += profile_wait
                    # 同水位完整重建；没有额度的请求不物化历史，也不产生物理事实。
                    if endpoint.auth is not None: endpoint.client.api_key = await endpoint.auth.access_token()
                    messages = model_messages if model_messages is not None else history(self.threads.snapshot_messages(thread_id, through=through, raw=True)['messages'])
                    physical = uuid.uuid4().hex
                    try:
                        evidence = redact_credentials({**selected, 'model': endpoint.model_name}, secrets=(endpoint.secret,))
                        sequence = self.threads.record_provider_request(thread_id, provider_options=evidence,
                            physical_request_id=physical, retry_of=retry_of, through=through, tool_choice_resolution=resolution)
                    except Exception as error: raise ThreadWriteError('required Thread request could not be saved') from error
                    if retry_of is None: retry_of = sequence
                    provider_started = time.perf_counter()
                    try:
                        async with asyncio.timeout(settings.get('timeout', endpoint.timeout)):
                            async with endpoint.model.request_stream(messages, settings, params) as stream:
                                try:
                                    async for event in stream:
                                        pass
                                except BaseException:
                                    partial = encode(stream.get())
                                    raise
                                result = stream.get()
                    finally: timing['provider_s'] = time.perf_counter() - provider_started
                    timing['duration_s'] = time.perf_counter() - started
                    typed = encode(result)
                    try:
                        message_seq = self.threads.record_model_response(thread_id, physical, endpoint.id, typed,
                            {'response': {'usage': _usage(result)}, 'timing': timing})
                    except Exception as error: raise ThreadWriteError('required typed response could not be saved') from error
                    incomplete = None
                    if result.finish_reason not in ('stop', 'tool_call'):
                        incomplete = 'output_token_limit' if result.finish_reason == 'length' else 'provider_incomplete'
                    if endpoint.kind == 'siwc' and (result.provider_details or {}).get('finish_reason') != 'completed':
                        incomplete = incomplete or 'provider_incomplete'
                    return result, message_seq, incomplete
            except ThreadWriteError: raise
            except asyncio.CancelledError:
                if physical is not None:
                    timing['duration_s'] = time.perf_counter() - started
                    self._event(thread_id, physical, endpoint, 'cancelled', {'timing': timing, 'partial_response': partial})
                raise
            except Exception as error:
                if physical is not None:
                    timing['duration_s'] = time.perf_counter() - started
                    self._event(thread_id, physical, endpoint, 'error', {'error': redact_credentials(str(error), secrets=(endpoint.secret,)), 'timing': timing, 'partial_response': partial})
                if physical is None: raise
                if not _transient(error, endpoint.kind) or attempt + 1 == self.max_attempts:
                    reason = 'context_limit' if 'context' in str(error).lower() and getattr(error, 'status_code', None) == 400 else 'provider_request_error'
                    raise ProviderFailure(reason, redact_credentials(str(error), secrets=(endpoint.secret,))) from error
                traceback.clear_frames(error.__traceback__)
            finally: messages = result = partial = None
            await asyncio.sleep(self.retry_delay)

    def _event(self, thread_id, physical, endpoint, kind, payload):
        try:
            self.threads.record_provider_event(thread_id, 'provider_' + kind,
                {'physical_request_id': physical, 'endpoint': endpoint.id, 'payload': payload})
        except Exception as error: raise ThreadWriteError('required Thread response could not be saved') from error


def model_plugin(profiles, *, threads=('threads', 'threads'), name='models', request_limit=None):
    from .plugins import Plugin
    def install(context):
        store = context.require(*threads)
        providers = {}
        for profile, options in profiles.items():
            provider = ModelProvider(threads=store, request_limit=request_limit, **options)
            context.on_close(provider.close)
            providers[profile] = provider
        context.provide('models', providers)
    return Plugin(name, (threads[0],), install)


RESOURCE_SCHEMA = (
    'CREATE TABLE resource_calls(id TEXT PRIMARY KEY NOT NULL,kind TEXT NOT NULL,endpoint TEXT NOT NULL,model TEXT NOT NULL,last_seq INTEGER NOT NULL)',
    'CREATE INDEX resource_calls_kind ON resource_calls(kind,id)',
    'CREATE TABLE resource_events(call_id TEXT NOT NULL,seq INTEGER NOT NULL,kind TEXT NOT NULL,raw_bytes INTEGER NOT NULL,PRIMARY KEY(call_id,seq))',
    'CREATE TABLE resource_chunks(call_id TEXT NOT NULL,seq INTEGER NOT NULL,chunk INTEGER NOT NULL,raw_start INTEGER NOT NULL,raw_bytes INTEGER NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(call_id,seq,chunk))',
)+usage.schema("resource")


class ResourceCalls:
    """物理请求正文存一份；逻辑调用与 Thread 使用小引用关联。"""
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _append(writer, identifier, kind, payload):
        sequence = writer.query('SELECT last_seq FROM resource_calls WHERE id=?',(identifier,))[0][0]+1
        raw_bytes = index = 0
        def emit(size, body):
            nonlocal raw_bytes, index
            writer.execute('INSERT INTO resource_chunks VALUES(?,?,?,?,?,?)', (identifier, sequence, index, raw_bytes, size, body))
            raw_bytes += size
            index += 1
        writer.write_json_chunks(payload, emit)
        writer.execute('INSERT INTO resource_events VALUES(?,?,?,?)',(identifier,sequence,kind,raw_bytes))
        writer.execute('UPDATE resource_calls SET last_seq=? WHERE id=?',(sequence,identifier))
        return sequence

    def begin(self, kind, endpoint, model, payload):
        import uuid
        identifier = uuid.uuid4().hex
        def write(writer):
            writer.execute('INSERT INTO resource_calls VALUES(?,?,?,?,0)',(identifier,kind,endpoint,model))
            self._append(writer,identifier,'request',payload)
            if kind=='embedding':usage.begin(writer,'resource',identifier,model)
            elif kind=='embedding_use':usage.logical_embedding(writer,model,payload)
        self.store.transaction(write)
        return identifier

    def event(self, identifier, kind, payload):
        def write(writer):
            sequence=self._append(writer,identifier,kind,payload)
            if (kind in ('response','error','decode_error','cancelled')
                    and writer.query('SELECT kind FROM resource_calls WHERE id=?',(identifier,))[0][0]=='embedding'):
                usage.finish(writer,'resource',identifier,outcome=kind,body=payload.get('response'),timing=payload.get('timing'))
            return sequence
        return self.store.transaction(write)

    def read(self, identifier):
        from ._json_chunks import decode_chunks
        def read(view):
            return [{'kind':kind,'payload':decode_chunks(body for (body,) in view.iter_query(
                'SELECT payload FROM resource_chunks WHERE call_id=? AND seq=? ORDER BY chunk',(identifier,sequence)))}
                for sequence,kind in view.iter_query('SELECT seq,kind FROM resource_events WHERE call_id=? ORDER BY seq',(identifier,))]
        return self.store.read(read)


@dataclass
class _Vector:
    values: list
    source: tuple
    attempts: tuple


class EmbeddingProvider(_Provider):
    """待办按原文去重与有界微批；向量始终按各逻辑原文位置返还。"""
    def __init__(self, endpoints, store, threads=None, *, dimensions=None, max_attempts=2,
                 retry_delay=.1, cache_max_items=5000, cache_max_bytes=64*1024*1024,
                 http_connections=None, batch_texts=50, batch_chars=100000, batch_wait_ms=5,
                 request_limit=None):
        super().__init__(endpoints, max_attempts=max_attempts, retry_delay=retry_delay,
                         request_limit=request_limit, http_connections=http_connections, embedding=True)
        if batch_texts < 1 or batch_chars < 1 or batch_wait_ms < 0: raise ValueError('invalid microbatch limits')
        if cache_max_items < 0 or cache_max_bytes < 0: raise ValueError('invalid cache limits')
        self.calls, self.threads = ResourceCalls(store), threads
        self.dimensions = dimensions
        self.batch_texts, self.batch_chars, self.batch_wait = batch_texts, batch_chars, batch_wait_ms / 1000
        self.cache_max_items, self.cache_max_bytes = cache_max_items, cache_max_bytes
        self._cache, self._cache_bytes = OrderedDict(), 0
        self._pending, self._queues, self._flushers = {}, {}, {}

    def _enqueue(self, text, dimensions):
        key = (dimensions, text)
        if key in self._cache:
            self._cache.move_to_end(key)
            future = asyncio.get_running_loop().create_future()
            future.set_result(self._cache[key][0])
            return future
        if key in self._pending: return self._pending[key]
        future = asyncio.get_running_loop().create_future()
        self._pending[key] = future
        self._queues.setdefault(dimensions, deque()).append(key)
        if dimensions not in self._flushers:
            worker = self._group.create_task(self._flush(dimensions))
            self._flushers[dimensions] = worker
        return future

    def _remember(self, key, value):
        size = len(key[1].encode('utf-8')) + len(value.values) * 32
        if not self.cache_max_items or size > self.cache_max_bytes: return
        self._cache[key] = (value, size)
        self._cache_bytes += size
        while len(self._cache) > self.cache_max_items or self._cache_bytes > self.cache_max_bytes:
            _, (_, removed) = self._cache.popitem(last=False)
            self._cache_bytes -= removed

    async def _flush(self, dimensions):
        capacity = sum(endpoint.capacity for endpoint in self.endpoints)
        gate = asyncio.Semaphore(capacity)
        async def run_batch(batch):
            try:
                values = await self._physical([key[1] for key in batch], dimensions)
            except BaseException as error:
                for index, key in enumerate(batch):
                    future = self._pending.pop(key)
                    if not future.done():
                        if isinstance(error, asyncio.CancelledError): future.cancel()
                        elif isinstance(error, ThreadWriteError): future.set_exception(error)
                        else:
                            failed = ProviderFailure('embedding_error', str(error))
                            failed.embedding_sources = tuple((call, index) for call in getattr(error, 'embedding_attempts', ()))
                            future.set_exception(failed)
                if isinstance(error, asyncio.CancelledError): raise
            else:
                for key, value in zip(batch, values, strict=True):
                    self._remember(key, value)
                    future = self._pending.pop(key)
                    if not future.done(): future.set_result(value)
            finally: gate.release()
        try:
            await asyncio.sleep(self.batch_wait)
            while self._queues.get(dimensions):
                async with asyncio.TaskGroup() as group:
                    while self._queues.get(dimensions):
                        await gate.acquire()
                        batch, chars = [], 0
                        queue = self._queues[dimensions]
                        while queue and len(batch) < self.batch_texts:
                            if batch and chars + len(queue[0][1]) > self.batch_chars: break
                            key = queue.popleft()
                            chars += len(key[1])
                            batch.append(key)
                        group.create_task(run_batch(batch))
        finally:
            for key in self._queues.pop(dimensions, []): self._pending.pop(key).cancel()
            self._flushers.pop(dimensions, None)

    async def _physical(self, texts, dimensions):
        endpoint = self._select()
        attempts = []
        for attempt in range(self.max_attempts):
            identifier = None
            timing = {}
            started = time.perf_counter()
            try:
                async with endpoint.resources.acquire() as timing:
                    settings = {'truncate': False}
                    if dimensions is not None and endpoint.send_dimensions: settings['dimensions'] = dimensions
                    try:
                        identifier = self.calls.begin('embedding', endpoint.id, endpoint.model_name,
                            {'texts': texts, 'dimensions': dimensions, 'retry_of': attempts[0] if attempts else None})
                    except Exception as error: raise ThreadWriteError('required embedding request could not be saved') from error
                    attempts.append(identifier)
                    provider_started = time.perf_counter()
                    try:
                        async with asyncio.timeout(endpoint.timeout):
                            result = await endpoint.model.embed(texts, input_type='document', settings=settings)
                    finally: timing['provider_s'] = time.perf_counter() - provider_started
                    timing['duration_s'] = time.perf_counter() - started
                    vectors = [list(vector) for vector in result.embeddings]
                    self._embedding_event(identifier, 'response', {'response': {'vectors': vectors, 'usage': _usage(result), 'provider_details': result.provider_details, 'provider_response_id': result.provider_response_id, 'model_name': result.model_name, 'provider_name': result.provider_name}, 'timing': timing})
                    if len(vectors) != len(texts) or (dimensions is not None and any(len(vector) != dimensions for vector in vectors)):
                        raise ValueError('embedding response count or dimensions differ from request')
                    return [_Vector(vector, (identifier, index), tuple((call, index) for call in attempts))
                            for index, vector in enumerate(vectors)]
            except ThreadWriteError: raise
            except BaseException as error:
                if identifier is not None:
                    timing['duration_s'] = time.perf_counter() - started
                    self._embedding_event(identifier, 'cancelled' if isinstance(error, asyncio.CancelledError) else 'error',
                        {'error': redact_credentials(str(error), secrets=(endpoint.secret,)), 'timing': timing,
                         **({'partial_response': error.body} if isinstance(error, APIResponseValidationError) else {})})
                if isinstance(error, asyncio.CancelledError): raise
                if identifier is None or not _transient(error, endpoint.kind) or attempt + 1 == self.max_attempts:
                    failure = ProviderFailure('embedding_error', redact_credentials(str(error), secrets=(endpoint.secret,)))
                    failure.embedding_attempts = tuple(attempts)
                    raise failure from error
                traceback.clear_frames(error.__traceback__)
            await asyncio.sleep(self.retry_delay)

    def _embedding_event(self, identifier, kind, payload):
        try: self.calls.event(identifier, kind, payload)
        except Exception as error: raise ThreadWriteError('required embedding response could not be saved') from error

    @_operation
    async def embed(self, texts, *, metadata, dimensions=None):
        if metadata.get('thread_id') is not None:
            if self.threads is None: raise ThreadWriteError('Thread reference requires a ThreadStore')
            if self.threads.describe(metadata['thread_id'])['actor'] != metadata['actor']:
                raise ValueError('embedding actor differs from Thread owner')
        texts = list(texts)
        futures = [self._enqueue(text, dimensions if dimensions is not None else self.dimensions) for text in texts]
        # 同文 future 被多个主体共享，某一调用取消不取消其他主体的物理工作。
        results = await asyncio.gather(*(asyncio.shield(future) for future in futures), return_exceptions=True)
        sources = []
        for position, result in enumerate(results):
            attempts = getattr(result, 'embedding_sources', ()) if isinstance(result, BaseException) else result.attempts
            sources.extend({'call_id': call, 'item_index': index, 'input_index': position} for call, index in attempts)
        try:
            identifier = self.calls.begin('embedding_use', '', self.endpoints[0].model_name,
                {'metadata': metadata, 'sources': sources, 'status': 'failed' if any(isinstance(value, BaseException) for value in results) else 'completed'})
            if metadata.get('thread_id') is not None: self.threads.event(metadata['thread_id'], 'resource_call_ref', {'call_id': identifier})
        except Exception as error: raise ThreadWriteError('required logical embedding evidence could not be saved') from error
        for result in results:
            if isinstance(result, BaseException): raise result
        return [list(result.values) for result in results]

    __call__ = embed


def embedding_plugin(profiles, *, storage=('storage', 'store'), threads=('threads', 'threads'), name='embeddings', request_limit=None):
    from .plugins import Plugin
    def install(context):
        store = context.require(*storage)
        thread_store = context.require(*threads) if threads is not None else None
        providers = {}
        for profile, options in profiles.items():
            provider = EmbeddingProvider(store=store, threads=thread_store, request_limit=request_limit, **options)
            context.on_close(provider.close)
            providers[profile] = provider
        context.provide('embeddings', providers)
    requires = tuple(dict.fromkeys([storage[0]] + ([threads[0]] if threads is not None else [])))
    return Plugin(name, requires, install, schema=RESOURCE_SCHEMA)
