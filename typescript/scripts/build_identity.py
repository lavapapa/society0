"""只读采集构建身份，保留Git原生差异与未跟踪输入的实际内容。"""
from pathlib import Path
import os, subprocess

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
