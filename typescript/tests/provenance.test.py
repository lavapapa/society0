from pathlib import Path
import json, subprocess, tempfile, shutil, os, sys
root=Path(__file__).resolve().parents[1]
directory=tempfile.mkdtemp(prefix='pr01-provenance-', dir=os.environ['TMPDIR'])
probe=Path(directory)
for name in ['scripts','patches','vendor','vendor-baseline']: (probe/name).mkdir()
shutil.copyfile(root/'scripts/build.py',probe/'scripts/build.py')
shutil.copyfile(root/'scripts/build_entries.py',probe/'scripts/build_entries.py')
shutil.copyfile(root/'scripts/build_compile.py',probe/'scripts/build_compile.py')
shutil.copyfile(root/'scripts/build_native.py',probe/'scripts/build_native.py')
shutil.copyfile(root/'scripts/build_identity.py',probe/'scripts/build_identity.py')
for name in ['vendor','vendor-baseline']: shutil.copyfile(root/name/'source-manifest.json',probe/name/'source-manifest.json')
shutil.copyfile(root/'package-lock.json',probe/'package-lock.json')
series=json.loads((root/'patches/series.json').read_text())
for item in series['patches']: shutil.copyfile(root/'patches'/item['file'],probe/'patches'/item['file'])
(probe/'patches/series.json').write_text(json.dumps(series))
subprocess.run(['git','init','-q'],cwd=probe,check=True)
subprocess.run(['git','add','.'],cwd=probe,check=True)
subprocess.run(['git','-c','user.name=Provenance fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture'],cwd=probe,check=True)
print(json.dumps({'case':'RV3-01-T01','fixture':str(probe)}),flush=True)
for case in ['unknown-source','missing-series','wrong-order']:
    source=json.loads((root/'vendor/source-manifest.json').read_text())
    current=json.loads(json.dumps(series))
    if case=='unknown-source': source['repository']=''
    if case=='missing-series': current.pop('patches')
    if case=='wrong-order': current['patches'][0]['order']=2
    (probe/'vendor/source-manifest.json').write_text(json.dumps(source))
    (probe/'patches/series.json').write_text(json.dumps(current))
    result=subprocess.run([sys.executable,'-B','scripts/build.py'],cwd=probe,capture_output=True,text=True)
    assert result.returncode!=0,case
    assert 'AssertionError' in result.stderr,result.stderr
    assert not (probe/'.build/release.json').exists(),case
    print(case,'rejected:',result.stderr.strip().splitlines()[-1])
