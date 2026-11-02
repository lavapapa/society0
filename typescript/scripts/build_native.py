"""从Git声明输入装配Native包，并保留真实输出作为复用参照。"""
from pathlib import Path
import json, shutil, subprocess, sys

NATIVE_OUTPUTS = {'sqlite-native': ['build/sqlite_native.node', 'vendor/libsqlite3.so', 'NATIVE-BUILD.json'],
                  'random-native': ['build/random_native.node', 'NATIVE-BUILD.json']}


def git_paths(root, *args):
    return set(subprocess.check_output(['git', 'ls-files', '-z', *args], cwd=root).decode().split('\0')) - {''}


def tracked(root, relative):
    return sorted(git_paths(root, '--cached', '--others', '--exclude-standard', '--', relative)
                  - git_paths(root, '--deleted', '--', relative))


def copy_inputs(root, original, names, destination):
    for name in names:
        relative = original if name == '.' else original + '/' + name
        for path in tracked(root, relative):
            source = root / path
            assert source.is_file(), 'INPUT_MISSING ' + path
            target = destination / Path(path).relative_to(original)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


def verify_native(build, folder):
    package = build / folder; reference = build / 'native-work' / folder
    for relative in NATIVE_OUTPUTS[folder]:
        actual, expected = package / relative, reference / relative
        assert actual.is_file() and expected.is_file(), f'built dependency identity mismatch: {folder}/{relative} missing artifact or reference'
        assert actual.read_bytes() == expected.read_bytes(), f'built dependency identity mismatch: {folder}/{relative}'
    record = json.loads((package / 'NATIVE-BUILD.json').read_text())
    assert record['outputs'] == NATIVE_OUTPUTS[folder][:-1], f'built dependency identity mismatch: {folder} output declaration'


def assemble_native(root, build, folder, original):
    assert folder in NATIVE_OUTPUTS
    destination = build / folder; destination.mkdir()
    declaration = json.loads((root / original / 'package.json').read_text())
    shutil.copyfile(root / original / 'package.json', destination / 'package.json')
    work = build / 'native-work' / folder
    assert work.resolve() == build.resolve() / 'native-work' / folder
    if work.exists(): shutil.rmtree(work)
    subprocess.run([sys.executable, '-B', str(root / original / 'build.py'), '--out-dir', str(work)], check=True)
    copy_inputs(root, original, [name for name in declaration['files'] if name not in NATIVE_OUTPUTS[folder]], destination)
    for relative in NATIVE_OUTPUTS[folder]:
        target = destination / relative; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(work / relative, target)
    verify_native(build, folder)
