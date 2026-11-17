from pathlib import Path
import json, os, sys
from native_build import compile_atomic, node_headers, output_directory, prefix_maps, write_record


def main():
    package = Path(__file__).resolve().parent
    output = output_directory(package)
    headers = node_headers(); maps = prefix_maps(package, output, headers)
    source = json.loads((package / 'vendor/SOURCE.json').read_text())
    upstream = package / ('vendor/random123-' + source['commit'])
    cxx = os.environ.get('CXX', 'clang++' if sys.platform == 'darwin' else 'g++')
    link = ['-undefined', 'dynamic_lookup', '-install_name', '@rpath/random_native.node', '-Wl,-no_uuid'] if sys.platform == 'darwin' else []
    compile_atomic([cxx, '-std=c++17', '-shared', '-fPIC', '-O2', '-Wall', '-Wextra', '-DNAPI_VERSION=8',
                    '-frandom-seed=random_native', *maps, '-I' + str(headers), '-I' + str(upstream / 'include'),
                    str(package / 'source/addon.cc'), *link], output, 'build/random_native.node')
    write_record(output, ['build/random_native.node'], {'cxx': cxx}, {'sourceIdentity': source})


if __name__ == '__main__':
    main()
