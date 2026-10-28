from pathlib import Path
import subprocess,json,shutil,sys
root=Path(__file__).resolve().parents[1]
previous=Path(sys.argv[1]).resolve()
out=Path(sys.argv[2]).resolve()
assert not out.exists() and not out.is_relative_to(root)
(out/'tarballs').mkdir(parents=True);(out/'consumer').mkdir()
subprocess.run(['node',str(root/'node_modules/typescript/bin/tsc'),'-p',str(root/'.build/cli/tsconfig.json')],check=True)
result=subprocess.run(['npm','pack','--ignore-scripts','--json','--pack-destination',str(out/'tarballs')],cwd=root/'.build/cli',check=True,text=True,capture_output=True)
record=json.loads(result.stdout)[0]
(out/'tarballs'/ (record['filename']+'.json')).write_text(result.stdout)
consumer=out/'consumer'
(consumer/'package.json').write_text(json.dumps({'name':'society0-cli-consumer','version':'1.0.0','private':True,'type':'module'}))
packages=[str(p) for p in (previous/'tarballs').glob('*.tgz') if not p.name.startswith('society0-cli-')]+[str(out/'tarballs'/record['filename'])]
subprocess.run(['npm','install','--offline','--ignore-scripts',*packages],cwd=consumer,check=True)
shutil.copyfile(root/'tests/cli.test.mjs',consumer/'cli.test.mjs')
subprocess.run(['node','--test','cli.test.mjs'],cwd=consumer,check=True)
(out/'result.json').write_text(json.dumps({'status':'passed','cliTarball':str(out/'tarballs'/record['filename']),'unchangedPackagesFrom':str(previous/'tarballs')},indent=2))
