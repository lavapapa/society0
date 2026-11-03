from pathlib import Path
import argparse,json,os,sys,tarfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from release_compare import path_leaks

def implementation_identity(package, source, expected_commit):
    implementation=source.get('implementation')
    message='ARTIFACT_IMPLEMENTATION '+package['name']
    assert isinstance(implementation,dict),message
    assert (implementation.get('package'),implementation.get('version'))==(package['name'],package['version']),message
    assert implementation.get('gitCommit')==expected_commit,message
    assert implementation.get('dirty') is False,message
    assert implementation.get('release') is True,message
    assert implementation.get('lockfile')=='BUILD-LOCK.json',message


def native_outputs(archive,package,references):
    outputs={'@society0/sqlite-native':['build/sqlite_native.node','vendor/libsqlite3.so'],
             '@society0/random-native':['build/random_native.node']}.get(package['name'])
    if outputs is None:return
    folder=package['name'].split('/')[-1];members=set(archive.getnames())
    assert 'package/NATIVE-BUILD.json' in members,'ARTIFACT_NATIVE_RECORD '+folder
    record=json.load(archive.extractfile('package/NATIVE-BUILD.json'))
    assert record['outputs']==outputs,'ARTIFACT_NATIVE_OUTPUTS '+folder
    for relative in outputs+['NATIVE-BUILD.json']:
        member='package/'+relative;reference=references/folder/relative
        assert member in members,'ARTIFACT_NATIVE_MEMBER '+member
        assert reference.is_file(),'ARTIFACT_NATIVE_REFERENCE '+str(reference)
        assert archive.extractfile(member).read()==reference.read_bytes(),'ARTIFACT_NATIVE_BYTES '+member

def sidecar_identity(archive,expected_commit):
    member='package/generated/plugin.meta.json'
    if member not in archive.getnames():return
    source=json.load(archive.extractfile(member))['source']
    assert source.get('gitCommit')==expected_commit,'ARTIFACT_SIDECAR_IDENTITY commit'
    assert source.get('dirty') is False,'ARTIFACT_SIDECAR_IDENTITY dirty'

def targets(value):
    if isinstance(value,str):yield value
    elif isinstance(value,dict):
        for sub in value.values():yield from targets(sub)

def source_distribution(package,members):
    assert 'workspace:' not in json.dumps(package),package['name']
    native=package['name'] in ['@society0/sqlite-native','@society0/random-native']
    for name in members:
        if not name.startswith('package/src/'):continue
        assert native and name.endswith(('.mjs','.js','.d.ts')),name

def provenance_files(archive,members):
    for required in ['LICENSE','SOURCE.json','BUILD-LOCK.json','PATCHES/series.json','PATCHES/0001-native-lifecycle.patch']:
        assert 'package/'+required in members,required
    series=json.load(archive.extractfile('package/PATCHES/series.json'))
    for index,patch in enumerate(series['patches'],1):
        assert patch['order']==index and 'package/PATCHES/'+patch['file'] in members,patch

def declared_targets(package,members):
    for kind in ['exports','bin']:
        for target in targets(package.get(kind,{})):
            assert 'package/'+target.removeprefix('./') in members,target

def declarations(archive,members):
    for member in members:
        if member.endswith('.d.ts'):
            assert 'const enum ' not in archive.extractfile(member).read().decode(),member

def consumer_modules(artifacts,consumer_root=None):
    if consumer_root is None:return artifacts/'consumer/node_modules'
    modules=consumer_root.resolve()/'node_modules'
    assert modules.is_dir(),'ARTIFACT_CONSUMER_ROOT '+str(consumer_root)
    return modules

def installed_location(artifacts,package,consumer_root=None):
    root=consumer_modules(artifacts,consumer_root)
    if root.is_dir():
        installed=root/package['name']
        assert installed.is_dir() and not installed.is_symlink(),installed
        assert installed.resolve().is_relative_to(root),installed

def check_archive(file,artifacts,expected_commit,consumer_root=None):
    with tarfile.open(file) as archive:
        members=set(archive.getnames())
        package=json.load(archive.extractfile('package/package.json'))
        implementation_identity(package,json.load(archive.extractfile('package/SOURCE.json')),expected_commit)
        native_outputs(archive,package,artifacts/'native-reference')
        sidecar_identity(archive,expected_commit)
        source_distribution(package,members)
        provenance_files(archive,members)
        declared_targets(package,members)
        declarations(archive,members)
        installed_location(artifacts,package,consumer_root)
        leaks=path_leaks(file,[str(Path(__file__).resolve().parents[1]),str(artifacts),os.environ['HOME'],os.environ['TMPDIR']])
        assert not leaks,leaks
        return package['name']

def main(artifacts,expected_commit,consumer_root=None):
    modules=consumer_modules(artifacts,consumer_root)
    checked=[check_archive(file,artifacts,expected_commit,consumer_root) for file in sorted((artifacts/'tarballs').glob('society0-*.tgz'))]
    expected=json.loads((artifacts/'release.json').read_text())['packages']
    assert len(checked)==len(expected),(checked,expected)
    print(json.dumps({'tarballChecks':'passed','installationChecks':'required' if consumer_root is not None else ('colocated' if modules.is_dir() else 'not-requested'),'consumerRoot':str(modules.parent),'packages':checked,'checks':['expected clean release implementation','exports and bins exist','JS and declaration distribution','no workspace protocol','physical consumer packages when consumer exists','runtime enum declarations','licenses, source, lock and ordered patches']},indent=2))

def arguments(argv):
    parser=argparse.ArgumentParser(description='核验发行归档；安装后使用显式消费者目录检查物理包。')
    parser.add_argument('artifacts',type=Path)
    parser.add_argument('expected_commit')
    parser.add_argument('--consumer-root',type=Path,help='实际安装目录，必须包含node_modules；指定后执行安装态检查')
    return parser.parse_args(argv)

if __name__=='__main__':
    args=arguments(sys.argv[1:])
    main(args.artifacts.resolve(),args.expected_commit,args.consumer_root)

# cases: RV3-01-T02
