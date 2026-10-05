"""X05 临时 Bashkit 选型实验；不接入产品依赖。"""
import asyncio
import gc
import importlib.metadata
import json
import platform
import resource
import statistics
import subprocess
import sys
import time

from bashkit import Bash


def rss():
    return int(subprocess.check_output(['ps', '-o', 'rss=', '-p', str(__import__('os').getpid())])) * 1024


def checked(bash, command):
    result = bash.execute_sync(command)
    assert result.exit_code == 0, result.stderr
    return result.stdout


def pipelines():
    bash = Bash(files={'/rows': '1\n2\n3\n'})
    assert checked(bash, 'cat /rows | head -n 1') == '1\n'
    assert checked(bash, 'tail -n 1 /rows') == '3\n'
    assert checked(bash, '''echo '{"x":2}' | jq '.x' ''') == '2\n'
    checked(bash, 'mkdir -p /workspace; echo private > /workspace/note')
    saved = bash.snapshot()
    restored = Bash.from_snapshot(saved, files={'/rows': '1\n2\n3\n'})
    assert restored.read_file('/workspace/note') == 'private\n'
    return {'pipeline_equal': True, 'workspace_restored': True, 'snapshot_bytes': len(saved)}


def lazy(size=8 * 1024 * 1024):
    calls = []
    def load():
        calls.append(size)
        return 'x\n' * (size // 2)
    before = rss()
    bash = Bash(files={'/large': load})
    assert calls == []
    t = time.perf_counter()
    assert checked(bash, 'head -n 1 /large') == 'x\n'
    first = time.perf_counter() - t
    after = rss()
    t = time.perf_counter()
    assert checked(bash, 'head -n 1 /large') == 'x\n'
    repeat = time.perf_counter() - t
    assert calls == [size]
    del bash
    gc.collect()
    return {'input_bytes': size, 'returned_bytes': 2, 'provider_calls': len(calls),
            'first_seconds': first, 'repeat_seconds': repeat,
            'rss_before': before, 'rss_materialized': after, 'rss_after_release': rss()}


async def scoped_builtin():
    calls = []
    async def data(ctx):
        await asyncio.sleep(0)
        calls.append(list(ctx.argv))
        # 实际接入时由该闭包绑定主体、权限和固定 revision。
        if ctx.argv[0] == 'query':
            return json.dumps({'revision': 'r1', 'total': 1, 'records': [{'id': 'allowed'}]})
        return json.dumps({'revision': 'r1', 'content': 'complete', 'next_offset': None})
    bash = Bash(custom_builtins={'data': data})
    result = await bash.execute('data query /visible | jq .total; data read /visible/allowed | jq -r .content')
    assert result.exit_code == 0, result.stderr
    assert result.stdout == '1\ncomplete\n'
    checked(bash, 'mkdir -p /workspace; echo note > /workspace/note')
    restored = Bash.from_snapshot(bash.snapshot(), custom_builtins={'data': data})
    assert restored.read_file('/workspace/note') == 'note\n'
    assert (await restored.execute('data query /visible | jq .total')).stdout == '1\n'
    return {'calls': calls, 'async_callback_and_restore': True}


def activations(count=100):
    times = []
    before = rss()
    for _ in range(count):
        t = time.perf_counter()
        bash = Bash()
        assert checked(bash, 'echo ready') == 'ready\n'
        del bash
        times.append(time.perf_counter() - t)
    gc.collect()
    released = rss()
    retained = [Bash() for _ in range(20)]
    active = rss()
    del retained
    gc.collect()
    return {'count': count, 'median_seconds': statistics.median(times),
            'p95_seconds': sorted(times)[int(count * .95)-1],
            'rss_before': before, 'rss_released': released,
            'rss_20_retained': active, 'rss_final': rss()}


def transfer(size=8 * 1024 * 1024):
    payload = 'a' * size
    bash = Bash(custom_builtins={'data': lambda ctx: payload})
    before = rss()
    t = time.perf_counter()
    result = bash.execute_sync('data')
    elapsed = time.perf_counter() - t
    assert result.stdout == payload[:len(result.stdout)]
    assert result.stdout_truncated == (len(result.stdout) < size)
    return {'bytes': size, 'seconds': elapsed, 'rss_before': before, 'rss_after': rss(),
            'full_output_equal': result.stdout == payload, 'returned_bytes': len(result.stdout),
            'stdout_truncated': result.stdout_truncated, 'exit_code': result.exit_code}


def main():
    mode = sys.argv[1]
    result = asyncio.run(scoped_builtin()) if mode == 'builtin' else globals()[mode]()
    result.update(mode=mode, python=platform.python_version(), platform=platform.platform(),
                  bashkit=importlib.metadata.version('bashkit'), peak_rss_native=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
