from pathlib import Path
import ctypes, json, os, platform, sys
from native_build import compile_atomic, node_headers, output_directory, prefix_maps, write_record


def library_options(path, expected):
    library = ctypes.CDLL(str(path), mode=os.RTLD_LOCAL | os.RTLD_NOW | os.RTLD_DEEPBIND)
    library.sqlite3_compileoption_get.restype = ctypes.c_char_p
    library.sqlite3_sourceid.restype = ctypes.c_char_p
    assert library.sqlite3_sourceid().decode() == expected, 'SQLite source identity differs'
    options = []; index = 0
    while value := library.sqlite3_compileoption_get(index):
        options.append(value.decode()); index += 1
    return options


def main():
    package = Path(__file__).resolve().parent
    output = output_directory(package)
    assert sys.platform == 'linux' and platform.machine() in ['x86_64', 'amd64'], 'Supported build target: Linux x86_64/glibc'
    headers = node_headers(); maps = prefix_maps(package, output, headers)
    recipe = json.loads((package / 'vendor/BUILD.json').read_text())
    source = json.loads((package / 'vendor/SOURCE.json').read_text())
    cc, cxx = os.environ.get('CC', 'gcc'), os.environ.get('CXX', 'g++')
    compile_atomic([cc, '-std=c11', '-shared', '-fPIC', '-O2', '-pthread', '-frandom-seed=libsqlite3', *maps,
                    *['-DSQLITE_' + value for value in recipe['definitions']], str(package / 'vendor/source/sqlite3.c'), '-lm', '-ldl'],
                   output, 'vendor/libsqlite3.so')
    options = library_options(output / 'vendor/libsqlite3.so', source['sourceIdentity'])
    compile_atomic([cxx, '-std=c++17', '-shared', '-fPIC', '-O2', '-Wall', '-Wextra', '-frandom-seed=sqlite_native', *maps,
                    '-I' + str(headers), '-I' + str(package / 'vendor/source'), str(package / 'native/addon.cc'), '-ldl'],
                   output, 'build/sqlite_native.node')
    write_record(output, ['build/sqlite_native.node', 'vendor/libsqlite3.so'], {'cc': cc, 'cxx': cxx},
                 {'sourceIdentity': source['sourceIdentity'], 'compileOptions': options, 'buildRecipe': 'vendor/BUILD.json'})


if __name__ == '__main__':
    main()
