"""核对发行清单并选择已冻结的生产归档与验收输入。"""
from pathlib import Path
import json
from release import archive_records

def load_candidate(candidate):
    candidate = Path(candidate).resolve()
    assert (candidate / 'manifest.json').is_file(), 'CANDIDATE_MANIFEST ' + str(candidate / 'manifest.json')
    manifest = json.loads((candidate / 'manifest.json').read_text())
    release = json.loads((candidate / 'release.json').read_text())
    identity_error = 'CANDIDATE_IDENTITY ' + str(candidate)
    assert manifest['format'] == 'society-release/v1', identity_error
    assert manifest['release'] is True, identity_error
    assert release['release'] is True, identity_error
    assert manifest['implementation']['dirty'] is False, identity_error
    assert manifest['implementation'] == release['implementation'], identity_error
    records = archive_records(candidate / 'tarballs', manifest['implementation']['gitCommit'])
    assert records == manifest['tarballs'], 'CANDIDATE_ARCHIVES ' + str(candidate / 'tarballs')
    assert len(release['packages']) == len(set(release['packages'])) == 13, identity_error
    inputs = candidate / 'acceptance-inputs'
    for name in ['tests', 'examples', 'profiles']:
        assert (inputs / name).is_dir(), 'CANDIDATE_INPUTS ' + str(inputs / name)
    assert (inputs / 'scripts/release_compare.py').is_file(), 'CANDIDATE_INPUTS ' + str(inputs / 'scripts/release_compare.py')
    return {'candidate': candidate, 'manifest': manifest, 'release': release, 'inputs': inputs, 'packages': [candidate / 'tarballs' / record['filename'] for record in records], 'mode': 'candidate'}


def installed_plugin_names(consumer):
    names = []
    for path in sorted((consumer / 'node_modules/@society0').glob('*/package.json')):
        package = json.loads(path.read_text())
        if package.get('society', {}).get('plugin', {}).get('metadata'): names.append(package['name'])
    return names


def prepare_installed_assets(inputs, consumer):
    import shutil
    # import.meta.resolve从脚本位置寻找包，示例须置于消费者目录内。
    for folder in ['examples', 'profiles']: shutil.copytree(inputs / folder, consumer / folder)


def profile_lock_command(consumer, mode):
    flags = ['--allow-dirty'] if mode == 'dirty-development' else []
    return ['node', str(consumer / 'examples/profile-lock.mjs'), *flags, str(consumer / 'module-lock.json'), *installed_plugin_names(consumer)]


def artifact_command(selected, inputs, consumer=None):
    command = ['python3', '-B', str(inputs / 'tests/artifacts.test.py'), str(selected['candidate']), selected['release']['implementation']['gitCommit']]
    if consumer is not None: command += ['--consumer-root', str(consumer)]
    return command
