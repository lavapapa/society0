from pathlib import Path
import ctypes, json, os, platform, sys
from native_build import compile_atomic, node_headers, output_directory, prefix_maps, write_record


def library_options(path, expected):
    library = ctypes.CDLL(str(path), mode=os.RTLD_LOCAL | os.RTLD_NOW | getattr(os, 'RTLD_DEEPBIND', 0))
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
    supported = ((sys.platform == 'linux' and platform.machine() in ['x86_64', 'amd64'])
                 or (sys.platform == 'darwin' and platform.machine() in ['x86_64', 'arm64']))
    assert supported, 'Supported build targets: Linux x86_64/glibc and macOS x86_64/arm64'
    headers = node_headers(); maps = prefix_maps(package, output, headers)
    recipe = json.loads((package / 'vendor/BUILD.json').read_text())
    source = json.loads((package / 'vendor/SOURCE.json').read_text())
    default_cc, default_cxx = ('clang', 'clang++') if sys.platform == 'darwin' else ('gcc', 'g++')
    cc, cxx = os.environ.get('CC', default_cc), os.environ.get('CXX', default_cxx)
    definitions = *['-DSQLITE_' + value for value in recipe['definitions']], str(package / 'vendor/source/sqlite3.c')
    if sys.platform == 'darwin':
        compile_atomic([cc, '-std=c11', '-dynamiclib', '-fPIC', '-O2', '-frandom-seed=libsqlite3',
                        '-install_name', '@rpath/libsqlite3.so', '-Wl,-no_uuid', *maps, *definitions],
                       output, 'vendor/libsqlite3.so')
    else:
        compile_atomic([cc, '-std=c11', '-shared', '-fPIC', '-O2', '-pthread', '-frandom-seed=libsqlite3', *maps,
                        *definitions, '-lm', '-ldl'],
                       output, 'vendor/libsqlite3.so')
    options = library_options(output / 'vendor/libsqlite3.so', source['sourceIdentity'])
    link = ['-undefined', 'dynamic_lookup', '-install_name', '@rpath/sqlite_native.node', '-Wl,-no_uuid'] if sys.platform == 'darwin' else ['-ldl']
    compile_atomic([cxx, '-std=c++17', '-shared', '-fPIC', '-O2', '-Wall', '-Wextra', '-frandom-seed=sqlite_native', *maps,
                    '-I' + str(headers), '-I' + str(package / 'vendor/source'), str(package / 'native/addon.cc'), *link],
                   output, 'build/sqlite_native.node')
    write_record(output, ['build/sqlite_native.node', 'vendor/libsqlite3.so'], {'cc': cc, 'cxx': cxx},
                 {'sourceIdentity': source['sourceIdentity'], 'compileOptions': options, 'buildRecipe': 'vendor/BUILD.json'})


if __name__ == '__main__':
    main()
