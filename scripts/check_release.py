"""检查公开发行物的文件范围，确认 Social 保留且展示与经济实现未打包。"""
from pathlib import Path, PurePosixPath
import sys
import tarfile
import zipfile

EXCLUDED = {'tools', 'skill', 'skills', 'research', 'industry_next',
            'society0_mechanisms', 'society0_collections', '__pycache__', '.venv', 'node_modules', 'target'}
SOCIAL = {'social.py', 'social_cognition.py', 'social_domain.py', 'social_models.py',
          'social_ranking.py', 'social_recommendation.py', 'social_semantic.py', 'social_topology.py'}


def check(path):
    if path.suffix == '.whl':
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    else:
        with tarfile.open(path) as archive:
            names = [item.name for item in archive if item.isfile()]
    for name in names:
        parts = PurePosixPath(name).parts
        if EXCLUDED.intersection(parts) or 'workbench' in name.lower():
            raise ValueError(f'{path.name}: excluded public path: {name}')
    if path.name.startswith('society0-'):
        published = {PurePosixPath(name).name for name in names if '/plugins/' in name}
        missing = SOCIAL - published
        if missing:
            raise ValueError(f'{path.name}: missing Social modules: {sorted(missing)}')
        if path.name.endswith('.tar.gz'):
            required = ('native/society0-filesystem/Cargo.toml',
                        'native/society0-filesystem/pyproject.toml',
                        'native/society0-filesystem/build.rs',
                        'native/society0-filesystem/src/lib.rs',
                        'scripts/release_smoke.py',
                        'constraints.txt',
                        'docs/site/content/assets/iframe-worker.js',
                        'docs/site/content/assets/mark.svg',
                        'docs/site/vendor/iframe-worker-LICENSE')
            for suffix in required:
                if not any(name.endswith('/' + suffix) for name in names):
                    raise ValueError(f'{path.name}: missing source dependency: {suffix}')
    print(f'{path.name}: {len(names)} files, public scope verified')


if __name__ == '__main__':
    artifacts = sorted(Path(sys.argv[1]).glob('*.whl')) + sorted(Path(sys.argv[1]).glob('*.tar.gz'))
    if not artifacts:
        raise SystemExit('No distributions found')
    for artifact in artifacts:
        check(artifact)
