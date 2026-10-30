"""把 .build 生产包与第三方依赖打成安装态 tarball；acceptance.py 与 host-quality-consumer.py 共用。"""
from pathlib import Path
import json, subprocess
THIRD_PARTY = ['@standard-schema/spec', 'js-yaml', 'argparse', 'p-limit', 'yocto-queue', 'resolve.exports', 'ajv', 'fast-deep-equal', 'fast-uri', 'json-schema-traverse', 'require-from-string', '@vscode/ripgrep', '@vscode/ripgrep-linux-x64', '@earendil-works/pi-ai', '@earendil-works/pi-agent-core', 'just-bash', '@modelcontextprotocol/sdk']
TYPED = ['semver', '@types/node', '@types/js-yaml', '@types/semver', 'undici-types']
def pack(root, path, tar):
    result = subprocess.run(['npm', 'pack', '--ignore-scripts', '--json', '--pack-destination', str(tar)], cwd=path, check=True, text=True, capture_output=True)
    record = json.loads(result.stdout)[0]
    if path.is_relative_to(root / '.build'):
        assert not any(f['path'].startswith('src/') and not (path.name in ['sqlite-native', 'random-native'] and (f['path'].endswith('.mjs') or f['path'].endswith('.js') or f['path'].endswith('.d.ts'))) for f in record['files']), 'production tarball contains runtime TS source'
        manifest = json.loads((path / 'package.json').read_text())
        assert 'workspace:' not in json.dumps(manifest), 'workspace protocol leaked into published metadata'
        assert any(f['path'] == 'BUILD-LOCK.json' for f in record['files'])
        assert any(f['path'] == 'PATCHES/series.json' for f in record['files'])
    (tar / (record['filename'] + '.json')).write_text(json.dumps(record, indent=2) + '\n')
    print('packed', record['filename'], flush=True)
    return tar / record['filename']
def dependencies(root, tar):
    names = [*THIRD_PARTY, *TYPED]
    return [pack(root, root / 'node_modules' / name, tar) for name in names]
