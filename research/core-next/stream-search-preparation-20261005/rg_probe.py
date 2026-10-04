"""宿主 rg 的隔离 stdin 实验；正文按块生成，stdout 同步消费。"""
import asyncio
import json
import statistics
import time
from pathlib import Path


async def search(parts, *, json_output=False):
    started = time.perf_counter()
    args = ['rg', '--no-config', '--text', '--encoding', 'none']
    args += ['--json'] if json_output else ['--only-matching', '--byte-offset', '--line-number']
    process = await asyncio.create_subprocess_exec(
        *args, '-e', '关(?:键)词', '-', stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    writes = 0
    input_bytes = 0

    async def feed():
        nonlocal writes, input_bytes
        for part in parts:
            process.stdin.write(part)
            await process.stdin.drain()
            writes += 1
            input_bytes += len(part)
        process.stdin.close()
        await process.stdin.wait_closed()

    async def output():
        # 统计总输出，同时仅保存极小预览；长行JSON不会完整驻留Python。
        total = 0
        preview = bytearray()
        while part := await process.stdout.read(65536):
            total += len(part)
            preview.extend(part[:max(0, 80-len(preview))])
        return total, preview.decode('utf-8')

    _, (total, preview), stderr = await asyncio.gather(feed(), output(), process.stderr.read())
    code = await process.wait()
    assert not stderr, stderr
    return dict(exit_code=code, writes=writes, input_bytes=input_bytes,
                output_bytes=total, preview=preview, elapsed_ms=(time.perf_counter()-started)*1000)


def long_line():
    # 关键词从65533偏移开始，跨越64KiB块边界；全程仅有64KiB临时块。
    marker = '关键词'.encode()
    for start in range(0, 1048576, 65536):
        data = bytearray(b'x' * 65536)
        for index, value in enumerate(marker):
            absolute = 65533 + index
            if start <= absolute < start + len(data):
                data[absolute-start] = value
        if start + len(data) == 1048576:
            data[-1] = 10
        yield data


async def main():
    word = '关键词'.encode()
    small = b'xxxxxxx' + word + b'\n'
    split = await search([small[i:i+2] for i in range(0,len(small),2)])
    assert split['preview'] == '1:7:关键词\n'
    long = await search(long_line())
    assert long['preview'] == '1:65533:关键词\n'
    long_json = await search(long_line(), json_output=True)
    docs = [await search([part.encode()]) for part in ['关键', '词', '关键词']]
    assert [doc['exit_code'] for doc in docs] == [1, 1, 0]
    timings = [(await search([small]))['elapsed_ms'] for _ in range(30)]
    version_process = await asyncio.create_subprocess_exec('rg','--version',stdout=asyncio.subprocess.PIPE)
    version, _ = await version_process.communicate()
    report = dict(rg_version=version.decode().splitlines()[0],
                  split_every_two_bytes=split, one_mib_line=long,
                  one_mib_line_json=long_json,
                  separate_documents=docs,
                  tiny_document_subprocess_ms=dict(n=30, median=statistics.median(timings), min=min(timings), max=max(timings)))
    Path(__file__).with_name('rg-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    asyncio.run(main())
