"""两类Native配方共享的封闭输出、真实编译与构建记录。"""
from pathlib import Path
import argparse, json, os, shutil, subprocess

PREFIX_MAPS = ['<package>=.', '<out>=.', '<node-headers>=node-headers']


def output_directory(package):
    parser = argparse.ArgumentParser(description='Offline native build outside the package source tree')
    parser.add_argument('--out-dir', type=Path, required=True)
    output = parser.parse_args().out_dir.resolve()
    assert not output.is_relative_to(package.resolve()), 'out-dir must be outside the package source tree'
    for name in ['build', 'vendor', 'tmp']:
        (output / name).mkdir(parents=True, exist_ok=True)
    return output


def node_headers():
    node = Path(subprocess.check_output(['node', '-p', 'process.execPath'], text=True).strip())
    headers = Path(os.environ.get('NODE_INCLUDE_DIR', str(node.parent.parent / 'include/node'))).resolve()
    assert (headers / 'node_api.h').is_file(), 'Node headers required; set NODE_INCLUDE_DIR'
    return headers


def prefix_maps(package, output, headers):
    mappings = [(package.resolve(), '.'), (output, '.'), (headers, 'node-headers')]
    return ['-ffile-prefix-map=' + str(path) + '=' + target
            for path, target in sorted(mappings, key=lambda item: len(str(item[0])))]


def compile_atomic(command, output, relative):
    target = (output / relative).resolve()
    assert target.parent in [(output / 'build').resolve(), (output / 'vendor').resolve()]
    temporary = target.with_name(target.stem + '.' + str(os.getpid()) + target.suffix)
    assert temporary.parent == target.parent
    env = os.environ.copy(); env['TMPDIR'] = str(output / 'tmp')
    actual = [*command, '-o', str(temporary)]
    print(' '.join(actual), flush=True)
    subprocess.run(actual, env=env, check=True)
    os.replace(temporary, target)


def write_record(output, outputs, compilers, details):
    versions = {name: subprocess.check_output([command, '--version'], text=True).splitlines()[0]
                for name, command in compilers.items()}
    record = {'format': 'society-native-build/v1', 'outputs': outputs,
              'flags': {'prefixMaps': PREFIX_MAPS, 'randomSeed': True},
              'compiler': versions, 'node': {'version': subprocess.check_output(['node', '--version'], text=True).strip()},
              **details}
    (output / 'NATIVE-BUILD.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
    temporary = output / 'tmp'
    assert temporary.resolve() == output / 'tmp'
    shutil.rmtree(temporary)
