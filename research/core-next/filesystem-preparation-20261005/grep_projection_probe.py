"""隔离验证真实 Bashkit 对当前分片投影的搜索语义，不修改产品。"""
import asyncio
import importlib.metadata
import json
from pathlib import Path
import tempfile

from society0.kernel.information_sql import SQLInformation, DocumentSpec
from society0.kernel.interaction import Information, InteractionScope, Moment, Actions
from society0.kernel.shell import ShellSession
from society0.kernel.storage import StageStore


async def main():
    # 1024字节投影预算对应768字节原文片；两个完整汉字落在边界两侧。
    boundary = (b'x' * 764 + b'\n' + '甲乙\n'.encode() + b'y' * 500)
    duplicates = b'AAA\n' + b'z' * 1100
    report = {'bashkit': importlib.metadata.version('bashkit'), 'cases': []}
    with tempfile.TemporaryDirectory(prefix='society0-vfs-grep-') as folder:
        root = Path(folder)
        with StageStore.create(root / 'run', ['CREATE TABLE docs(id INTEGER PRIMARY KEY,body BLOB NOT NULL)'],
                initialize=lambda w: w.executemany('INSERT INTO docs VALUES(?,?)', [(1, boundary), (2, duplicates)])) as store:
            info = Information(lambda *args: True)
            info.mount('/docs', SQLInformation('docs', store, {'texts': DocumentSpec('docs', 'id', 'body')}))
            scope = InteractionScope('alice', Moment(1, 'read'))
            shell = ShellSession(scope, info, Actions(lambda *args: True), result_dir=root / 'shell',
                projection_file_bytes=1024, projection_page_size=100)
            try:
                manifest = json.loads((await shell.execute('cat /world/docs/texts/1/@manifest.json')).stdout)
                text_dir = '/world/docs/texts/1/' + manifest['text_directory']
                checks = [
                    ('boundary_per_part', f"grep -r -n '甲乙' {text_dir}"),
                    ('boundary_reassembled', f"cat {text_dir}/*.txt | grep -n '甲乙'"),
                    ('representation_duplicates', "grep -r -l -E 'AAA|QUFB|total_bytes' /world/docs/texts/2"),
                    ('base64_false_positive', "grep -r -l -F 'QUFB' /world/docs/texts/2"),
                ]
                for name, command in checks:
                    result = await shell.execute(command)
                    report['cases'].append({'name': name, 'command': command, 'exit_code': result.exit_code,
                        'stdout': result.stdout, 'stderr': result.stderr})
                assert report['cases'][0]['exit_code'] == 1
                assert report['cases'][1]['exit_code'] == 0 and '甲乙' in report['cases'][1]['stdout']
                assert '@manifest.json' in report['cases'][2]['stdout']
                assert '@text-' in report['cases'][2]['stdout'] and '@parts-' in report['cases'][2]['stdout']
                assert b'QUFB' not in duplicates and report['cases'][3]['exit_code'] == 0
                report['findings'] = {
                    'utf8_safe_parts_miss_cross_part_word': True,
                    'recursive_search_includes_metadata_and_both_representations': True,
                    'base64_representation_can_match_absent_source_text': True,
                    'scope': '小规模确定性语义试验；未测性能与真实模型。',
                }
            finally:
                await shell.aclose()
                scope.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
