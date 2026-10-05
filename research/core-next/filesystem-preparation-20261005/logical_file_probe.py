"""同一逻辑文件的真实 Bashkit 整读与 SQLInformation 范围读取对照。"""
import asyncio
import importlib.metadata
import json
from pathlib import Path
import tempfile

from bashkit import Bash, FileSystem
from society0_filesystem import callback_filesystem
from society0.kernel.information_sql import SQLInformation, DocumentSpec
from society0.kernel.interaction import Information, InteractionScope, Moment
from society0.kernel.storage import StageStore


async def main():
    keyword = b'CROSS_BOUNDARY_KEYWORD'
    keyword_offset = 65530
    start, middle, tail = b'{"padding":"', b'","marker":"', b'","suffix":"'
    prefix = start + b'x' * (keyword_offset - len(start) - len(middle)) + middle
    ending = b'"}\n'
    body = prefix + keyword + tail
    body += b'z' * (1048576 - len(body) - len(ending)) + ending
    assert body.index(keyword) < 65536 < body.index(keyword) + len(keyword)
    assert json.loads(body)['marker'] == keyword.decode()
    report = {'bashkit': importlib.metadata.version('bashkit'), 'document_bytes': len(body),
        'keyword_offset': keyword_offset, 'logical_path': '/world/document.json', 'cases': [],
        'measurement': '记录 SQL 返回给 callback_filesystem 的正文实际字节；未测磁盘 I/O 或峰值内存。'}
    with tempfile.TemporaryDirectory(prefix='society0-logical-file-') as folder:
        root = Path(folder)
        output = root / 'output'
        output.mkdir()
        with StageStore.create(root / 'run', ['CREATE TABLE docs(id INTEGER PRIMARY KEY,body BLOB NOT NULL)'],
                initialize=lambda w: w.execute('INSERT INTO docs VALUES(?,?)', (1, body))) as store:
            info = Information(lambda *args: True)
            info.mount('/docs', SQLInformation('docs', store, {'texts': DocumentSpec('docs', 'id', 'body')}))
            scope = InteractionScope('alice', Moment(1, 'read'))
            bound = info.bound(scope)
            calls = []

            async def callback(operation, path):
                if path == '/':
                    if operation == 'list': return [('document.json', 'file', len(body), 0o444, 0, 0)]
                    if operation == 'stat': return ('directory', 0, 0o555, 0, 0)
                    if operation == 'exists': return True
                if path != '/document.json': raise FileNotFoundError(path)
                if operation == 'stat':
                    stat = await bound.stat('/docs/texts/1')
                    return ('file', stat.total_bytes, 0o444, 0, 0)
                if operation == 'exists': return True
                if operation == 'read':
                    chunk = await bound.read('/docs/texts/1', offset=0, size=len(body), expected_revision=0)
                    calls.append({'offset': 0, 'requested_bytes': len(body), 'returned_bytes': len(chunk.data)})
                    return chunk.data
                raise ValueError(operation)

            bash = Bash(mounts=[{'host_path': str(output), 'vfs_path': '/out', 'writable': True}],
                        allowed_mount_paths=[str(output)])
            bash.mount('/world', FileSystem.from_capsule(callback_filesystem(callback)), read_only=True)
            commands = [
                ('cat', 'cat /world/document.json > /out/copied.json'),
                ('head', 'head -c 32 /world/document.json'),
                ('grep', "grep -o -F 'CROSS_BOUNDARY_KEYWORD' /world/document.json"),
                ('jq', "jq -r '.marker' /world/document.json"),
            ]
            for name, command in commands:
                calls.clear()
                result = await bash.execute(command)
                case = {'name': name, 'command': command, 'exit_code': result.exit_code,
                    'stdout': result.stdout, 'stderr': result.stderr,
                    'read_file_calls': list(calls), 'supplied_bytes': sum(c['returned_bytes'] for c in calls)}
                if name == 'cat':
                    case['original_bytes_equal'] = (output / 'copied.json').read_bytes() == body
                    assert case['original_bytes_equal'] and result.exit_code == 0
                elif name == 'head':
                    assert result.stdout.encode() == body[:32] and result.exit_code == 0
                elif name == 'grep':
                    assert result.stdout.strip() == keyword.decode() and result.exit_code == 0
                elif result.exit_code == 0:
                    assert result.stdout.strip() == keyword.decode()
                if result.exit_code == 0:
                    assert case['supplied_bytes'] >= len(body)
                report['cases'].append(case)
            chunk = await bound.read('/docs/texts/1', offset=keyword_offset, size=64, expected_revision=0)
            report['sql_range'] = {'offset': keyword_offset, 'requested_bytes': 64,
                'returned_bytes': len(chunk.data), 'total_bytes': chunk.total_bytes,
                'original_bytes_equal': chunk.data == body[keyword_offset:keyword_offset+64],
                'contains_cross_boundary_keyword': keyword in chunk.data,
                'next_offset': chunk.next_offset, 'revision': chunk.revision}
            assert report['sql_range']['original_bytes_equal'] and len(chunk.data) == 64
            scope.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
