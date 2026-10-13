"""静态来源组合：独立位置与保存后效果复用既有完整认知输入。"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace

from ..async_utils import invoke_maybe_async
from ..kernel.cognition import CognitiveInput, InputBatch
from ..kernel.plugins import Plugin
from ..kernel.runtime import Session


@dataclass(frozen=True)
class SourcePosition:
    position: object


@dataclass(frozen=True)
class SourceBatch:
    messages: list[dict]
    position: object
    effects: tuple = field(default=(), kw_only=True, repr=False, compare=False)


SourceRead = Callable[[Session, SourcePosition | None], SourceBatch | Awaitable[SourceBatch]]


def _source_ids(ids):
    seen = set()
    for source_id in ids:
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError('cognition source id must be a nonempty string')
        if source_id in seen:
            raise ValueError(f'duplicate cognition source id: {source_id}')
        seen.add(source_id)


@dataclass(frozen=True)
class SourceSpec:
    id: str
    source: tuple[str, str]

    def __post_init__(self):
        _source_ids((self.id,))
        if (not isinstance(self.source, tuple) or len(self.source) != 2
                or any(not isinstance(part, str) or not part.strip() for part in self.source)):
            raise ValueError('cognition source reference must contain plugin and service names')


class CompositePerception:
    def __init__(self, sources: tuple[tuple[str, SourceRead], ...]):
        self.sources = tuple(sources)
        _source_ids(source_id for source_id, read in self.sources)

    async def __call__(self, session: Session, previous: dict[str, object] | None) -> SourceBatch:
        session.scope.check_active()
        if previous is not None and not isinstance(previous, dict):
            raise TypeError('cognition position must be a source-id mapping')
        positions = {} if previous is None else dict(previous)
        messages, effects = [], []
        for source_id, read in self.sources:
            before = SourcePosition(positions[source_id]) if source_id in positions else None
            batch = await invoke_maybe_async(read, session, before)
            session.scope.check_active()
            messages.extend(batch.messages)
            positions[source_id] = batch.position
            effects.extend(batch.effects)
        return SourceBatch(messages, positions, effects=tuple(effects))


class ComposedInput:
    def __init__(self, threads, perception: CompositePerception, *, consumer='operating_context',
                 environment='', precision=None, reminders=None):
        self.threads, self.perception = threads, perception
        self.consumer, self.environment = consumer, environment
        self.precision, self.reminders = precision, reminders

    async def __call__(self, session: Session) -> InputBatch:
        effects = []
        async def perceive(current, previous):
            combined = await self.perception(current, previous)
            effects.extend(combined.effects)
            return combined.messages, combined.position
        batch = await CognitiveInput(self.threads, perceive, consumer=self.consumer,
                                     environment=self.environment, precision=self.precision,
                                     reminders=self.reminders)(session)
        return replace(batch, effects=tuple(effects))


def cognition_plugin(*, sources: tuple[SourceSpec, ...], threads=('threads', 'threads'),
                     consumer='operating_context', environment='', precision=None, reminders=None,
                     name='cognition') -> Plugin:
    sources = tuple(sources)
    _source_ids(spec.id for spec in sources)
    def install(ctx):
        perception = CompositePerception(tuple((spec.id, ctx.require(*spec.source)) for spec in sources))
        ctx.provide('input_builder', ComposedInput(ctx.require(*threads), perception, consumer=consumer,
                    environment=environment, precision=precision, reminders=reminders))
    requires = tuple(dict.fromkeys((threads[0], *(spec.source[0] for spec in sources))))
    return Plugin(name, requires=requires, install=install)
