"""用合成 wheel/sdist 检查公开范围，无需构建或第三方运行时依赖。"""
import io
from pathlib import Path
import runpy
import subprocess
import sys
import tarfile
import zipfile

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'check_release.py'
check = runpy.run_path(str(SCRIPT))['check']
SOCIAL = ('social.py', 'social_cognition.py', 'social_domain.py', 'social_models.py',
          'social_ranking.py', 'social_recommendation.py', 'social_semantic.py', 'social_topology.py')
SOURCE = ('native/society0-filesystem/Cargo.toml',
          'native/society0-filesystem/pyproject.toml',
          'native/society0-filesystem/build.rs',
          'native/society0-filesystem/src/lib.rs',
          'scripts/release_smoke.py',
          'constraints.txt',
          'docs/site/content/assets/iframe-worker.js',
          'docs/site/content/assets/mark.svg',
          'docs/site/vendor/iframe-worker-LICENSE')


def public_files(kind):
    package = 'society0/' if kind == 'wheel' else 'src/society0/'
    files = [package + '__init__.py', package + 'kernel/runtime.py']
    files += [package + 'plugins/' + name for name in SOCIAL]
    if kind == 'sdist':
        files += list(SOURCE)
    return files


def archive(tmp_path, kind, files):
    if kind == 'wheel':
        path = tmp_path / 'society0-7.0.0-py3-none-any.whl'
        with zipfile.ZipFile(path, 'w') as output:
            for name in files:
                output.writestr(name, b'# synthetic\n')
    else:
        path = tmp_path / 'society0-7.0.0.tar.gz'
        with tarfile.open(path, 'w:gz') as output:
            for name in files:
                body = b'# synthetic\n'
                member = tarfile.TarInfo('society0-7.0.0/' + name)
                member.size = len(body)
                output.addfile(member, io.BytesIO(body))
    return path


@pytest.mark.parametrize('kind', ['wheel', 'sdist'])
def test_core_and_all_social_are_public(tmp_path, kind, capsys):
    path = archive(tmp_path, kind, public_files(kind))
    check(path)
    assert 'public scope verified' in capsys.readouterr().out
    # 隔离 site-packages，确认发行检查脚本可由纯标准库 Python 执行。
    result = subprocess.run([sys.executable, '-I', '-S', str(SCRIPT), str(tmp_path)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert path.name in result.stdout


@pytest.mark.parametrize('kind', ['wheel', 'sdist'])
@pytest.mark.parametrize('excluded', [
    'docs/site/content/guide/workbench.md',
    'tools/workbench/app.py',
    'skill/SKILL.md',
    'skills/research/SKILL.md',
    'src/industry_next/__init__.py',
    'src/society0_mechanisms/__init__.py',
    'src/society0_collections/__init__.py',
    'docs/site/.venv/lib/local.py',
])
def test_private_paths_are_rejected(tmp_path, kind, excluded):
    path = archive(tmp_path, kind, public_files(kind) + [excluded])
    with pytest.raises(ValueError, match='excluded public path'):
        check(path)


@pytest.mark.parametrize('kind', ['wheel', 'sdist'])
@pytest.mark.parametrize('missing', SOCIAL)
def test_every_social_module_is_required(tmp_path, kind, missing):
    files = [name for name in public_files(kind) if not name.endswith('/' + missing)]
    with pytest.raises(ValueError, match='missing Social modules'):
        check(archive(tmp_path, kind, files))


@pytest.mark.parametrize('missing', SOURCE)
def test_sdist_requires_native_source_and_smoke_entry(tmp_path, missing):
    files = [name for name in public_files('sdist') if name != missing]
    with pytest.raises(ValueError, match='missing source dependency'):
        check(archive(tmp_path, 'sdist', files))
