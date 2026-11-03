from pathlib import Path
import json,sys,tarfile

def implementation_identity(package, source, expected_commit):
    implementation=source.get('implementation')
    message='ARTIFACT_IMPLEMENTATION '+package['name']
    assert isinstance(implementation,dict),message
    assert (implementation.get('package'),implementation.get('version'))==(package['name'],package['version']),message
    assert implementation.get('gitCommit')==expected_commit,message
    assert implementation.get('dirty') is False,message
    assert implementation.get('release') is True,message
    assert implementation.get('lockfile')=='BUILD-LOCK.json',message


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

def installed_location(artifacts,package):
    root=artifacts/'consumer/node_modules'
    if root.is_dir():
        installed=root/package['name']
        assert installed.is_dir() and not installed.is_symlink(),installed
        assert installed.resolve().is_relative_to(root),installed

def check_archive(file,artifacts,expected_commit):
    with tarfile.open(file) as archive:
        members=set(archive.getnames())
        package=json.load(archive.extractfile('package/package.json'))
        implementation_identity(package,json.load(archive.extractfile('package/SOURCE.json')),expected_commit)
        source_distribution(package,members)
        provenance_files(archive,members)
        declared_targets(package,members)
        declarations(archive,members)
        installed_location(artifacts,package)
        return package['name']

def main(artifacts,expected_commit):
    checked=[check_archive(file,artifacts,expected_commit) for file in sorted((artifacts/'tarballs').glob('society0-*.tgz'))]
    expected=json.loads((artifacts/'release.json').read_text())['packages']
    assert len(checked)==len(expected),(checked,expected)
    print(json.dumps({'tarballChecks':'passed','packages':checked,'checks':['expected clean release implementation','exports and bins exist','JS and declaration distribution','no workspace protocol','physical consumer packages when consumer exists','runtime enum declarations','licenses, source, lock and ordered patches']},indent=2))

if __name__=='__main__':main(Path(sys.argv[1]).resolve(),sys.argv[2])
