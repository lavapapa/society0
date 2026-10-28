"""仓外 tarball 验收；传入一个新的绝对输出目录。"""
from pathlib import Path
import json, subprocess, shutil, sys
root=Path(__file__).resolve().parents[1]
out=Path(sys.argv[1]).resolve()
assert not out.is_relative_to(root), 'consumer must be outside repository'
assert not out.exists(), 'use a fresh evidence directory'
out.mkdir(parents=True)
tar=out/'tarballs';tar.mkdir()
consumer=out/'consumer';consumer.mkdir()
def write(path, value):
    path.write_text(value if isinstance(value,str) else json.dumps(value,indent=2)+'\n')
def run(args,cwd=consumer):
    print('+',*map(str,args),flush=True)
    subprocess.run(list(map(str,args)),cwd=cwd,check=True)
def pack(path):
    result=subprocess.run(['npm','pack','--ignore-scripts','--json','--pack-destination',str(tar)],cwd=path,check=True,text=True,capture_output=True)
    record=json.loads(result.stdout)[0]
    if path.is_relative_to(root/'.build'):
        assert not any(f['path'].startswith('src/') for f in record['files']), 'production tarball contains TS source'
        manifest=json.loads((path/'package.json').read_text())
        assert 'workspace:' not in json.dumps(manifest), 'workspace protocol leaked into published metadata'
        assert any(f['path']=='BUILD-LOCK.json' for f in record['files'])
        assert any(f['path']=='PATCHES/series.json' for f in record['files'])
    write(tar/(record['filename']+'.json'),record)
    print('packed',record['filename'],flush=True)
    return tar/record['filename']
release=json.loads((root/'.build/release.json').read_text())
packages=[pack(root/'.build'/folder) for folder in release['packages']]
lab=root/'node_modules'
for name in ['@standard-schema/spec','js-yaml','argparse']:
    packages.append(pack(lab/name))
for name in ['semver','@types/node','@types/js-yaml','@types/semver','undici-types']:
    packages.append(pack(root/'node_modules'/name))
write(consumer/'package.json',{'name':'society0-outside-consumer','version':'1.0.0','private':True,'type':'module'})
run(['npm','install','--offline','--ignore-scripts',*packages])
for name in ['provider','consumer','jsdoc']:
    folder=consumer/'fixture-build'/name
    shutil.copytree(root/'tests/fixtures'/name,folder/'src')
    manifest={'name':'@fixtures/'+name,'version':'1.0.0','type':'module','files':['dist'],'exports':{'.':{'types':'./dist/index.d.ts','default':'./dist/index.js'}},'peerDependencies':{'@society0/host':'^0.1.0'}}
    if name=='provider': manifest['exports']['./service']={'types':'./dist/service.d.ts','default':'./dist/service.js'}
    else: manifest['peerDependencies']['@fixtures/provider']='1.0.0'
    write(folder/'package.json',manifest)
    write(folder/'tsconfig.json',{'compilerOptions':{'target':'ES2024','module':'NodeNext','strict':True,'isolatedModules':True,'skipLibCheck':False,'declaration':True,'allowJs':True,'checkJs':True,'rootDir':'src','outDir':'dist','types':['node']},'include':['src/**/*']})
    run(['node',root/'node_modules/typescript/bin/tsc','-p',folder/'tsconfig.json'])
    plugin=pack(folder)
    run(['npm','install','--offline','--ignore-scripts',plugin])
run(['npm','ls','--all'])
shutil.copyfile(root/'tests/consumer-types.ts',consumer/'consumer-types.ts')
write(consumer/'tsconfig.json',{'compilerOptions':{'target':'ES2024','module':'NodeNext','strict':True,'isolatedModules':True,'skipLibCheck':False,'noEmit':True,'types':['node']},'include':['consumer-types.ts']})
run(['node',root/'node_modules/typescript/bin/tsc','-p','tsconfig.json'])
shutil.copyfile(root/'tests/consumer.test.mjs',consumer/'consumer.test.mjs')
shutil.copyfile(root/'tests/cli.test.mjs',consumer/'cli.test.mjs')
write(consumer/'empty-boot.json','[]\n')
shutil.copytree(root/'tests/native',consumer/'native-tests')
run(['node','--test','consumer.test.mjs','cli.test.mjs',*[str(p.relative_to(consumer)) for p in (consumer/'native-tests').glob('*.test.ts')]])
run(['node','node_modules/@society0/cli/dist/main.js','--boot','empty-boot.json'])
write(out/'result.json',{'status':'passed','node':subprocess.check_output(['node','--version'],text=True).strip(),'consumer':str(consumer),'release':release,'plugins':['@fixtures/provider','@fixtures/consumer','@fixtures/jsdoc']})
