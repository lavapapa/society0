"""直接消费已发行 Bashkit 与现有窄原生桥；不实例化运行核心。"""
import asyncio
import json
import tempfile
from pathlib import Path
from bashkit import Bash, FileSystem
from society0_filesystem import callback_filesystem


async def main():
    block = 65536
    body = b'x\n' * (8 * 1024 * 1024 // 2)
    names = [f'{i:06}' for i in range(len(body) // block)]
    reads = []
    async def callback(operation, path):
        name = path.strip('/')
        if operation == 'exists':return name == '' or name in names
        if operation == 'stat':return ('directory' if not name else 'file', 0 if not name else block, 0o555 if not name else 0o444, 0, 0)
        if operation == 'list':return [(n, 'file', block, 0o444, 0, 0) for n in names]
        if operation == 'read':
            offset = int(name) * block
            value = body[offset:offset + block]
            reads.append(len(value))
            return value
        raise ValueError(operation)
    with tempfile.TemporaryDirectory(prefix='core-fs-selection-') as temp:
        shell = Bash(mounts=[{'host_path': temp, 'vfs_path': '/out', 'writable': True}],allowed_mount_paths=[temp])
        shell.mount('/parts', FileSystem.from_capsule(callback_filesystem(callback)),read_only=True)
        first = await shell.execute('head -c 4 /parts/000000')
        assert first.exit_code == 0 and first.stdout == 'x\nx\n'
        first_bytes = sum(reads)
        reads.clear()
        last = await shell.execute('tail -n 1 /parts/000127')
        assert last.exit_code == 0 and last.stdout == 'x\n'
        last_bytes = sum(reads)
        reads.clear()
        result = await shell.execute('cat /parts/* > /out/all')
        assert result.exit_code == 0, result.stderr
        assert Path(temp, 'all').read_bytes() == body
        return {'source_bytes': len(body), 'part_bytes': block, 'parts': len(names),
                'head4_provider_bytes': first_bytes, 'tail_line_provider_bytes': last_bytes,
                'full_cat_provider_bytes': sum(reads), 'full_cat_equal': True,
                'max_single_callback_bytes': max(reads), 'original_body_resident_for_test': True}

if __name__ == '__main__':print(json.dumps(asyncio.run(main()),indent=2))
