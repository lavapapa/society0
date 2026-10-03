"""复用端点与连接池；将物理模型尝试绑定完整 Thread 请求。"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps

import openai

from ..resource_managers import LLMManager, EmbeddingManager, _safe_provider_payload


class ProviderFailure(RuntimeError):
    def __init__(self, reason, message):
        self.reason = reason
        super().__init__(message)


class ThreadWriteError(RuntimeError):
    pass


def _operation(method):
    @wraps(method)
    async def scoped(self,*args,**kwargs):
        if self._closed:
            raise RuntimeError('provider is closed')
        task=asyncio.current_task()
        self._operations.add(task)
        try:return await method(self,*args,**kwargs)
        finally:self._operations.discard(task)
    return scoped


class _Provider:
    def __init__(self):
        self._closed=False
        self._operations=set()
        self._close_task=None

    async def close(self):
        if asyncio.current_task() in self._operations:
            raise RuntimeError('provider cannot close from its own request')
        self._closed=True
        if self._close_task is None:
            async def drain():
                tasks=list(self._operations)
                for task in tasks:task.cancel()
                if tasks:await asyncio.gather(*tasks,return_exceptions=True)
                await self.manager.close()
            self._close_task=asyncio.create_task(drain())
        try:await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            await asyncio.shield(self._close_task)
            raise


@dataclass
class _Trace:
    threads: object
    thread_id: str
    options: dict
    through: int
    retry_of: int | None = None
    failed: bool = False
    raw_response: object = None


_trace = ContextVar('kernel_provider_trace', default=None)


class _Manager(LLMManager):
    @staticmethod
    def _provider_response_payload(response, *, secrets=()):
        payload = LLMManager._provider_response_payload(response, secrets=secrets)
        _trace.get().raw_response = payload
        return payload

    def _append_agent_thread_event_best_effort(self, trace_metadata, event_type, *, payload,
                                              provider_request_id, attempt_number, endpoint):
        trace = _trace.get()
        if trace is None:
            raise RuntimeError('provider request requires a Thread')
        if trace.failed:
            raise ThreadWriteError('Thread write already failed')
        try:
            if event_type == 'provider_request':
                sequence = trace.threads.record_request(
                    trace.thread_id, provider_options=_safe_provider_payload(trace.options, secrets=(endpoint.api_key,)),
                    physical_request_id=provider_request_id, retry_of=trace.retry_of, through=trace.through,
                )
                if trace.retry_of is None:
                    trace.retry_of = sequence
            else:
                trace.threads.event(trace.thread_id, event_type, {
                    'physical_request_id': provider_request_id, 'endpoint': endpoint.id,
                    'payload': {**payload, 'raw_response': trace.raw_response} if event_type == 'provider_response' else payload,
                })
        except Exception as error:
            trace.failed = True
            raise ThreadWriteError('required Thread evidence could not be saved') from error
        return True


class ModelProvider(_Provider):
    def __init__(self, endpoints, threads, *, max_attempts=2, retry_delay=0.1,
                 global_concurrency=None, http_connections=None, request_jitter=0.0, request_options=None):
        super().__init__()
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError('max_attempts must be positive')
        self.threads = threads
        self.request_options = dict(request_options or {})
        self.manager = _Manager(endpoints, global_concurrency=global_concurrency,
                                http_connections=http_connections, request_jitter=request_jitter)
        # SDK 已配置 max_retries=0；由本适配层明确区分传输失败和留证失败。
        self.manager._max_retries = 1
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay

    @_operation
    async def request(self, thread_id, options):
        endpoint = self.manager._select_endpoint()
        if endpoint is None:
            raise ProviderFailure('provider_unavailable', 'no model endpoint')
        payload, resolution = self.manager._resolve_tool_choice(endpoint, {**self.request_options, **options})
        snapshot = self.threads.snapshot_messages(thread_id)
        payload['messages'] = snapshot['messages']
        recorded_options = {key: value for key, value in payload.items() if key != 'messages'}
        recorded_options['model'] = endpoint.deployment_name if endpoint.provider_type == 'azure' and endpoint.deployment_name else endpoint.model
        trace = _Trace(self.threads, thread_id, recorded_options, snapshot['through'])
        token = _trace.set(trace)
        transient = (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError, TimeoutError)
        try:
            for attempt in range(self.max_attempts):
                trace.raw_response = None
                try:
                    return await self.manager._execute_request(endpoint, payload, tool_choice_resolution=resolution)
                except transient as error:
                    if attempt + 1 == self.max_attempts:
                        raise ProviderFailure('transport_error', str(error)) from error
                    await asyncio.sleep(self.retry_delay)
                except openai.BadRequestError as error:
                    code = getattr(error, 'code', None)
                    reason = 'context_limit' if code == 'context_length_exceeded' else 'provider_request_error'
                    raise ProviderFailure(reason, str(error)) from error
                except openai.APIStatusError as error:
                    raise ProviderFailure('provider_request_error', str(error)) from error
        finally:
            _trace.reset(token)



def model_plugin(profiles, *, threads=('threads', 'threads'), name='models'):
    """每个命名配置显式提供端点、请求默认值与资源限额。"""
    from .plugins import Plugin

    def install(context):
        store = context.require(*threads)
        providers = {}
        for profile, options in profiles.items():
            provider = ModelProvider(threads=store, **options)
            context.on_close(provider.close)
            providers[profile] = provider
        context.provide('models', providers)

    return Plugin(name, (threads[0],), install)


RESOURCE_SCHEMA = (
    'CREATE TABLE resource_calls(id TEXT PRIMARY KEY NOT NULL,kind TEXT NOT NULL,endpoint TEXT NOT NULL,model TEXT NOT NULL,last_seq INTEGER NOT NULL)',
    'CREATE INDEX resource_calls_kind ON resource_calls(kind,id)',
    'CREATE TABLE resource_events(call_id TEXT NOT NULL,seq INTEGER NOT NULL,kind TEXT NOT NULL,raw_bytes INTEGER NOT NULL,PRIMARY KEY(call_id,seq))',
    'CREATE TABLE resource_chunks(call_id TEXT NOT NULL,seq INTEGER NOT NULL,chunk INTEGER NOT NULL,raw_start INTEGER NOT NULL,raw_bytes INTEGER NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(call_id,seq,chunk))',
)


class ResourceCalls:
    """物理请求正文存一份；逻辑调用与 Thread 使用小引用关联。"""
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _append(writer, identifier, kind, payload):
        sequence = writer.query('SELECT last_seq FROM resource_calls WHERE id=?',(identifier,))[0][0]+1
        raw_bytes = 0
        def rows():
            nonlocal raw_bytes
            for index,(size,body) in enumerate(writer.encode_chunks(payload)):
                yield identifier,sequence,index,raw_bytes,size,body
                raw_bytes += size
        writer.executemany('INSERT INTO resource_chunks VALUES(?,?,?,?,?,?)',rows())
        writer.execute('INSERT INTO resource_events VALUES(?,?,?,?)',(identifier,sequence,kind,raw_bytes))
        writer.execute('UPDATE resource_calls SET last_seq=? WHERE id=?',(sequence,identifier))
        return sequence

    def begin(self, kind, endpoint, model, payload):
        import uuid
        identifier = uuid.uuid4().hex
        def write(writer):
            writer.execute('INSERT INTO resource_calls VALUES(?,?,?,?,0)',(identifier,kind,endpoint,model))
            self._append(writer,identifier,'request',payload)
        self.store.transaction(write)
        return identifier

    def event(self, identifier, kind, payload):
        return self.store.transaction(lambda writer:self._append(writer,identifier,kind,payload))

    def read(self, identifier):
        from ._json_chunks import decode_chunks
        def read(view):
            return [{'kind':kind,'payload':decode_chunks(body for (body,) in view.iter_query(
                'SELECT payload FROM resource_chunks WHERE call_id=? AND seq=? ORDER BY chunk',(identifier,sequence)))}
                for sequence,kind in view.iter_query('SELECT seq,kind FROM resource_events WHERE call_id=? ORDER BY seq',(identifier,))]
        return self.store.read(read)


class _SourcedVector(list):
    __slots__ = ('source','attempts')
    def __init__(self, values, source, attempts):
        super().__init__(values)
        self.source = source
        self.attempts = attempts


@dataclass
class _EmbeddingTrace:
    calls: ResourceCalls
    current: str | None = None
    sources: list = None
    attempts: list = None
    failed: bool = False


_embedding_trace = ContextVar('kernel_embedding_trace', default=None)


class _EmbeddingManager(EmbeddingManager):
    def __init__(self, endpoints, calls, *, threads, max_attempts, retry_delay, batch_texts, batch_chars, batch_wait_ms, **options):
        super().__init__(endpoints, **options)
        self.calls, self.max_attempts, self.retry_delay = calls, max_attempts, retry_delay
        self.threads = threads
        self._embedding_timeout_schedule = None
        self._split_on_failure = False
        self._microbatch_max_batch_texts = self._request_batch_size = batch_texts
        self._microbatch_max_batch_chars = batch_chars
        self._microbatch_max_wait_ms = batch_wait_ms

    def _append_cache_provenance(self, *args, **kwargs):
        # 各逻辑调用返回前统一登记源物理调用，缓存本身不复制正文。
        return None

    def _append_agent_thread_event_best_effort(self, trace_metadata, event_type, *, payload,
                                             provider_request_id, attempt_number, endpoint):
        trace = _embedding_trace.get()
        if trace.failed:
            raise ThreadWriteError('required resource evidence already failed')
        try:
            if event_type == 'embedding_provider_request':
                trace.current = self.calls.begin('embedding', endpoint.id, endpoint.model, payload)
                trace.attempts.append(trace.current)
            else:
                self.calls.event(trace.current,event_type.removeprefix('embedding_provider_'),payload)
                if event_type == 'embedding_provider_response':
                    trace.sources.extend((trace.current,index) for index in range(payload['vectors_returned']))
        except Exception as error:
            trace.failed = True
            raise ThreadWriteError('required resource evidence could not be saved') from error
        return True

    async def _execute_request(self, endpoint, texts, dimensions, metadata=None):
        trace = _EmbeddingTrace(self.calls, sources=[],attempts=[])
        token = _embedding_trace.set(trace)
        transient = (openai.APIConnectionError,openai.RateLimitError,openai.InternalServerError,TimeoutError)
        try:
            for attempt in range(self.max_attempts):
                trace.sources = []
                try:
                    result = await super()._execute_request(endpoint,texts,dimensions,metadata)
                    if any(len(vector)!=dimensions for vector in result['result']):
                        raise ValueError('embedding response dimension differs from profile')
                    result['result'] = [_SourcedVector(vector,source,tuple((call,index) for call in trace.attempts))
                        for index,(vector,source) in enumerate(zip(result['result'],trace.sources,strict=True))]
                    return result
                except transient as error:
                    if attempt+1 == self.max_attempts:
                        raise ProviderFailure('embedding_transport_error',str(error)) from error
                    await asyncio.sleep(self.retry_delay)
        except Exception as error:
            error.embedding_sources={text:tuple((call,index) for call in trace.attempts) for index,text in enumerate(texts)}
            raise
        finally:
            _embedding_trace.reset(token)

    def _record_logical_result(self, metadata, model, texts, results):
        # 等待中的相同文本共享 future，各逻辑消费者独立记录自己的位置与主体。
        sources=[]
        for position,(text,result) in enumerate(zip(texts,results)):
            attempts=(getattr(result,'embedding_sources',{}).get(text,()) if isinstance(result,BaseException)
                      else result.attempts)
            sources.extend({'call_id':call,'item_index':index,'input_index':position} for call,index in attempts)
        try:
            identifier=self.calls.begin('embedding_use','',model,{'metadata':metadata,'sources':sources,
                'status':'failed' if any(isinstance(result,BaseException) for result in results) else 'completed'})
            if metadata.get('thread_id') is not None:
                self.threads.event(metadata['thread_id'],'resource_call_ref',{'call_id':identifier})
        except Exception as error:
            raise ThreadWriteError('required logical resource evidence could not be saved') from error


class EmbeddingProvider(_Provider):
    def __init__(self, endpoints, store, threads=None, *, dimensions=None, max_attempts=2,
                 retry_delay=0.1, cache_max_items=5000, cache_max_bytes=64*1024*1024,
                 http_connections=None,batch_texts=50,batch_chars=100000,batch_wait_ms=5):
        super().__init__()
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError('max_attempts must be positive')
        for value in (batch_texts,batch_chars):
            if type(value) is not int or value < 1:
                raise ValueError("batch sizes must be positive integers")
        if type(batch_wait_ms) not in (int,float) or not 0 <= batch_wait_ms < float("inf"):
            raise ValueError("batch_wait_ms must be finite and nonnegative")
        self.calls, self.threads = ResourceCalls(store), threads
        self.manager = _EmbeddingManager(endpoints,self.calls,threads=threads,max_attempts=max_attempts,retry_delay=retry_delay,
                                         cache_max_items=cache_max_items,cache_max_bytes=cache_max_bytes,http_connections=http_connections,
                                         batch_texts=batch_texts,batch_chars=batch_chars,batch_wait_ms=batch_wait_ms)
        self.dimensions = dimensions

    @_operation
    async def embed(self, texts, *, metadata, dimensions=None):
        if metadata.get('thread_id') is not None:
            if self.threads is None:
                raise ThreadWriteError('Thread reference requires a ThreadStore')
            if self.threads.describe(metadata['thread_id'])['actor'] != metadata['actor']:
                raise ValueError('embedding actor differs from Thread owner')
        result = await self.manager.request(texts,dimensions=dimensions or self.dimensions,metadata=metadata)
        return [list(vector) for vector in result['result']]

    __call__ = embed



def embedding_plugin(profiles, *, storage=('storage','store'), threads=('threads','threads'),name='embeddings'):
    """共享物理调用表随插件声明；各用途选择具名嵌入配置。"""
    from .plugins import Plugin
    def install(context):
        store=context.require(*storage)
        thread_store=context.require(*threads) if threads is not None else None
        providers={}
        for profile,options in profiles.items():
            provider=EmbeddingProvider(store=store,threads=thread_store,**options)
            context.on_close(provider.close)
            providers[profile]=provider
        context.provide('embeddings',providers)
    requires=tuple(dict.fromkeys([storage[0]]+([threads[0]] if threads is not None else [])))
    return Plugin(name,requires,install,schema=RESOURCE_SCHEMA)
