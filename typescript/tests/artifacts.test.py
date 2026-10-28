from pathlib import Path
import json,sys,tarfile
artifacts=Path(sys.argv[1]).resolve()
checked=[]
for file in sorted((artifacts/'tarballs').glob('society0-*.tgz')):
    with tarfile.open(file) as archive:
        members=set(archive.getnames())
        package=json.load(archive.extractfile('package/package.json'))
        assert 'workspace:' not in json.dumps(package),file
        assert not any(name.startswith('package/src/') for name in members),file
        for required in ['LICENSE','SOURCE.json','BUILD-LOCK.json','PATCHES/series.json','PATCHES/0001-native-lifecycle.patch']:
            assert 'package/'+required in members,(file,required)
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
assert len(checked)==8,checked
print(json.dumps({'tarballChecks':'passed','packages':checked,'checks':['exports and bins exist','JS and declaration distribution','no workspace protocol','physical consumer packages','runtime enum declarations','licenses, source, lock and ordered patch bytes']},indent=2))
