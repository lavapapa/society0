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

artifacts=Path(sys.argv[1]).resolve()
expected_commit=sys.argv[2]
checked=[]
for file in sorted((artifacts/'tarballs').glob('society0-*.tgz')):
    with tarfile.open(file) as archive:
        members=set(archive.getnames())
        package=json.load(archive.extractfile('package/package.json'))
        implementation_identity(package,json.load(archive.extractfile('package/SOURCE.json')),expected_commit)
        assert 'workspace:' not in json.dumps(package),file
        assert not any(name.startswith('package/src/') and not (package['name'] in ['@society0/sqlite-native','@society0/random-native'] and (name.endswith('.mjs') or name.endswith('.js') or name.endswith('.d.ts'))) for name in members),file
        for required in ['LICENSE','SOURCE.json','BUILD-LOCK.json','PATCHES/series.json','PATCHES/0001-native-lifecycle.patch']:
            assert 'package/'+required in members,(file,required)
        series=json.load(archive.extractfile('package/PATCHES/series.json'))
        for index,patch in enumerate(series['patches'],1):
            assert patch['order']==index and 'package/PATCHES/'+patch['file'] in members,(file,patch)
        def targets(value):
            if isinstance(value,str): yield value
            elif isinstance(value,dict):
                for sub in value.values(): yield from targets(sub)
        for target in targets(package.get('exports',{})):
            assert 'package/'+target.removeprefix('./') in members,(file,target)
        for target in targets(package.get('bin',{})):
            assert 'package/'+target.removeprefix('./') in members,(file,target)
        installed=artifacts/'consumer/node_modules'/package['name']
        assert installed.resolve().is_relative_to(artifacts/'consumer/node_modules'),installed
        for member in members:
            if member.endswith('.d.ts'):
                text=archive.extractfile(member).read().decode()
                assert 'const enum ' not in text,(file,member)
        checked.append(package['name'])
expected=json.loads((artifacts/'release.json').read_text())['packages']
assert len(checked)==len(expected),(checked,expected)
print(json.dumps({'tarballChecks':'passed','packages':checked,'checks':['exports and bins exist','JS and declaration distribution','no workspace protocol','physical consumer packages','runtime enum declarations','licenses, source, lock and ordered patch bytes']},indent=2))
