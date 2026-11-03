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
