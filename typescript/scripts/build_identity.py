"""只读采集构建身份，保留Git原生差异与未跟踪输入的实际内容。"""
from pathlib import Path
import json, os, shutil, subprocess

INPUTS = ['packages', 'apps', 'scripts', 'vendor', 'vendor-baseline', 'patches', 'package-lock.json', 'package.json']


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root)


def status_entries(raw):
    fields = iter(raw.decode().split('\0')); result = []
    for field in fields:
        if not field: continue
        status, path = field[:2], field[3:]
        result.append((status, path))
        if 'R' in status or 'C' in status:
            result.append((status, next(fields)))
    return result


def untracked_evidence(top, entries):
    result = []
    for status, relative in entries:
        if status != '??': continue
        path = top / relative
        if path.is_symlink(): result.append({'path': relative, 'symlink': os.readlink(path)})
        else:
            assert path.is_file(), 'UNTRACKED_PATH_UNRESOLVED ' + relative
            result.append({'path': relative, 'content': path.read_bytes()})
    return result


def worktree_identity(root):
    top = Path(git(root, 'rev-parse', '--show-toplevel').decode().strip())
    commit = git(root, 'rev-parse', 'HEAD').decode().strip()
    entries = status_entries(git(root, 'status', '--porcelain=v1', '--untracked-files=all', '-z'))
    ignored = status_entries(git(root, 'status', '--porcelain=v1', '--ignored=matching', '--untracked-files=all', '-z', '--', *INPUTS))
    return {'gitCommit': commit, 'dirty': bool(entries), 'dirtyPaths': sorted({path for _, path in entries}),
            'ignoredInputs': [path for status, path in ignored if status == '!!'],
            'diff': git(top, 'diff', 'HEAD', '--binary'), 'untracked': untracked_evidence(top, entries)}


def validate_release(worktree, packages, reuse, bytecode_disabled):
    assert bytecode_disabled, 'RELEASE_BYTECODE_WRITES run build.py with python3 -B'
    assert not packages and not reuse, 'RELEASE_REQUIRES_FULL_BUILD'
    assert not worktree['dirty'], 'RELEASE_DIRTY_WORKTREE ' + ' '.join(worktree['dirtyPaths'][:20])
    assert not worktree['ignoredInputs'], 'RELEASE_IGNORED_INPUT ' + ' '.join(worktree['ignoredInputs'][:20])


def save_untracked(directory, entries):
    records = []
    for entry in entries:
        record = {'path': entry['path']}
        if 'content' in entry:
            target = directory / 'untracked' / entry['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(entry['content']); record['bytes'] = len(entry['content'])
        else: record['symlink'] = entry['symlink']
        records.append(record)
    return records


def save_worktree_evidence(build, worktree):
    directory = build / 'worktree-evidence'
    assert directory.resolve() == build.resolve() / 'worktree-evidence'
    if directory.exists(): shutil.rmtree(directory)
    directory.mkdir(parents=True)
    (directory / 'tracked.patch').write_bytes(worktree['diff'])
    record = {key: worktree[key] for key in ['gitCommit', 'dirty', 'dirtyPaths', 'ignoredInputs']}
    record.update(format='society-build-worktree/v1', diff='tracked.patch', untracked=save_untracked(directory, worktree['untracked']))
    (directory / 'identity.json').write_text(json.dumps(record, indent=2) + '\n')


def validate_dependency(package, provenance, folder, upstream, worktree):
    message = 'built dependency identity mismatch: ' + folder
    assert package['name'] == '@society0/' + folder and provenance['commit'] == upstream, message
    implementation = provenance.get('implementation', {})
    assert implementation.get('gitCommit') == worktree['gitCommit'], message
    assert implementation.get('dirty') is worktree['dirty'], message
