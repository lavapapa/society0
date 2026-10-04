"""冻结公开运行合同，复用主机与代码调度执行完整步骤。"""
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import logging

from .composition import compose
from .schedule import Progress


@dataclass(frozen=True)
class RunContract:
    release: dict
    dependencies: dict
    configuration: dict
    time: dict
    budgets: dict
    credential_env: tuple[str, ...] = ()


@dataclass
class RunPlan:
    plugins: object
    contract: RunContract
    schedule: tuple[str, str] = ('schedule', 'schedule')
    runtime: tuple[str, str] = ('runtime', 'runtime')


def _write_manifest(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        stream.flush()
        os.fsync(stream.fileno())


def _append_timing(path, value):
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))+'\n')


async def run_plan(path, plan, *, source=None, step=None):
    """调度读取下一计划，Runtime 执行并发布完整步骤。"""
    plugins = tuple(plan.plugins)
    # 配置在安装前冻结为公开 JSON；凭据由调用方通过环境引用配置。
    contract = json.loads(json.dumps(asdict(plan.contract), ensure_ascii=False, allow_nan=False))
    path = Path(path)
    state = None
    report = None
    try:
        async with compose(path, plugins, source=source, step=step) as host:
            store = host.service('storage', 'store')
            schedule = host.service(*plan.schedule)
            runtime = host.service(*plan.runtime)
            run_id = store.read(lambda view: view.run_id)
            _write_manifest(path/'runner.json', {
                'format': 1, 'run_id': run_id, 'contract': contract,
                'plugins': [{'name': item.name, 'requires': list(item.requires)} for item in plugins],
                'schedule': list(plan.schedule), 'runtime': list(plan.runtime),
                'effective_runtime': {'capacity': runtime.capacity,
                    'max_activations': runtime.max_activations},
                'source': None if source is None else {'path':str(Path(source).absolute()),'step':store.complete_step},
            })
            state = {'run_id': run_id, 'status':'running', 'complete_step':store.complete_step,
                     'diagnostic_errors':0}
            progress = Progress(path/'runner-status.json', run_id)
            def report():
                if not progress.update(**{key:value for key,value in state.items() if key!='run_id'}):
                    state['diagnostic_errors'] += 1
                    logging.getLogger(__name__).warning('runner status could not be saved')
            report()
            try:
                while (planned := await schedule.next_step(store.complete_step)) is not None:
                    number = store.complete_step + 1
                    try:
                        await runtime.run_step(number, planned.time, planned.phases)
                    finally:
                        state['complete_step'] = store.complete_step
                        timing = runtime.last_timing
                        if timing is not None:
                            try:
                                _append_timing(path/'timings.jsonl', timing)
                            except OSError:
                                state['diagnostic_errors'] += 1
                                logging.getLogger(__name__).warning('step timing could not be saved')
                        report()
            finally:
                state['complete_step'] = store.complete_step
    except BaseException as error:
        if state is not None:
            state.update(status='failed', error=type(error).__name__)
            report()
        raise
    state['status'] = 'completed'
    report()
    return state


def main():
    import argparse
    import asyncio
    from importlib import import_module
    parser = argparse.ArgumentParser(description='执行明确配置的 Society0 插件运行计划')
    parser.add_argument('--factory', required=True, help='公开计划工厂 module:function')
    parser.add_argument('--config', required=True, type=Path, help='公开配置 JSON；凭据通过环境提供')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source', type=Path, help='从已有完整运行创建新分支')
    parser.add_argument('--step', type=int, help='所选完整步骤')
    args = parser.parse_args()
    try:
        module, function = args.factory.split(':', 1)
        factory = getattr(import_module(module), function)
        plan = factory(json.loads(args.config.read_text(encoding='utf-8')))
        result = asyncio.run(run_plan(args.output, plan, source=args.source, step=args.step))
    except Exception as error:
        # 领域或提供方异常文本可能含业务原文；详情保留在运行事实与诊断中。
        print(json.dumps({'error':{'code':type(error).__name__},'run_path':str(args.output)},ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
