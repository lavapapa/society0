"""复用端点与连接池；将物理模型尝试绑定完整 Thread 请求。"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass

import openai

from ..resource_managers import LLMManager, EmbeddingManager, _safe_provider_payload


class ProviderFailure(RuntimeError):
    def __init__(self, reason, message):
        self.reason = reason
        super().__init__(message)


class ThreadWriteError(RuntimeError):
    pass


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


class ModelProvider:
    def __init__(self, endpoints, threads, *, max_attempts=2, retry_delay=0.1):
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError('max_attempts must be positive')
        self.threads = threads
        self.manager = _Manager(endpoints)
        # SDK 已配置 max_retries=0；由本适配层明确区分传输失败和留证失败。
        self.manager._max_retries = 1
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay

    async def request(self, thread_id, options):
        endpoint = self.manager._select_endpoint()
        if endpoint is None:
            raise ProviderFailure('provider_unavailable', 'no model endpoint')
        payload, resolution = self.manager._resolve_tool_choice(endpoint, dict(options))
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
        finally:
            _trace.reset(token)

    async def close(self):
        await self.manager.close()
