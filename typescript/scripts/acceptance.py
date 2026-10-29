"""仓外 tarball 验收；传入一个新的绝对输出目录。"""
from pathlib import Path
import json, subprocess, shutil, sys, re
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
        assert not any(f['path'].startswith('src/') and not (path.name in ['sqlite-native','random-native'] and (f['path'].endswith('.mjs') or f['path'].endswith('.js') or f['path'].endswith('.d.ts'))) for f in record['files']), 'production tarball contains runtime TS source'
        manifest=json.loads((path/'package.json').read_text())
        assert 'workspace:' not in json.dumps(manifest), 'workspace protocol leaked into published metadata'
        assert any(f['path']=='BUILD-LOCK.json' for f in record['files'])
        assert any(f['path']=='PATCHES/series.json' for f in record['files'])
    write(tar/(record['filename']+'.json'),record)
    print('packed',record['filename'],flush=True)
    return tar/record['filename']
release=json.loads((root/'.build/release.json').read_text())
write(out/'release.json',release)
shutil.copytree(root/'.build/acceptance',out/'inputs')
test_root=out/'inputs/tests'
fixture_root=test_root/'fixtures'
assert test_root.is_dir() and fixture_root.is_dir(), 'build first to freeze acceptance inputs'
packages=[pack(root/'.build'/folder) for folder in release['packages']]
lab=root/'node_modules'
for name in ['@standard-schema/spec','js-yaml','argparse','p-limit','yocto-queue','resolve.exports','ajv','fast-deep-equal','fast-uri','json-schema-traverse','require-from-string','@vscode/ripgrep','@vscode/ripgrep-linux-x64','@earendil-works/pi-ai','@earendil-works/pi-agent-core','just-bash','@modelcontextprotocol/sdk']:
    packages.append(pack(lab/name))
for name in ['semver','@types/node','@types/js-yaml','@types/semver','undici-types']:
    packages.append(pack(root/'node_modules'/name))
write(consumer/'package.json',{'name':'society0-outside-consumer','version':'1.0.0','private':True,'type':'module'})
run(['npm','install','--offline','--ignore-scripts',*packages])
for name in ['provider','consumer','jsdoc']:
    folder=consumer/'fixture-build'/name
    shutil.copytree(fixture_root/name,folder/'src')
    manifest={'name':'@fixtures/'+name,'version':'1.0.0','type':'module','files':['dist'],'exports':{'.':{'types':'./dist/index.d.ts','default':'./dist/index.js'}},'peerDependencies':{'@society0/host':'^0.1.0'}}
    if name=='provider': manifest['exports']['./service']={'types':'./dist/service.d.ts','default':'./dist/service.js'}
    else: manifest['peerDependencies']['@fixtures/provider']='1.0.0'
    write(folder/'package.json',manifest)
    write(folder/'tsconfig.json',{'compilerOptions':{'target':'ES2024','module':'NodeNext','strict':True,'isolatedModules':True,'skipLibCheck':False,'declaration':True,'allowJs':True,'checkJs':True,'rootDir':'src','outDir':'dist','types':['node']},'include':['src/**/*']})
    run(['node',root/'node_modules/typescript/bin/tsc','-p',folder/'tsconfig.json'])
    plugin=pack(folder)
    run(['npm','install','--offline','--ignore-scripts',plugin])
run(['npm','ls','--all'])
shutil.copyfile(test_root/'consumer-types.ts',consumer/'consumer-types.ts')
shutil.copyfile(test_root/'pr02-types.ts',consumer/'pr02-types.ts')
shutil.copyfile(test_root/'include-types.ts',consumer/'include-types.ts')
consumer_types=['consumer-types.ts','pr02-types.ts','include-types.ts']
if 'core' in release['packages']:
    for filename in ['ajv-consumer.ts','ripgrep-consumer.ts']:
        shutil.copyfile(test_root/filename,consumer/filename)
        consumer_types.append(filename)
write(consumer/'tsconfig.json',{'compilerOptions':{'target':'ES2024','module':'NodeNext','strict':True,'isolatedModules':True,'skipLibCheck':False,'noEmit':True,'types':['node']},'include':consumer_types})
run(['node',root/'node_modules/typescript/bin/tsc','-p','tsconfig.json'])
shutil.copyfile(test_root/'consumer.test.mjs',consumer/'consumer.test.mjs')
shutil.copyfile(test_root/'peer-contract.test.mjs',consumer/'peer-contract.test.mjs')
shutil.copyfile(test_root/'preflight-session.test.mjs',consumer/'preflight-session.test.mjs')
shutil.copyfile(test_root/'cli.test.mjs',consumer/'cli.test.mjs')
write(consumer/'pr02.test.mjs',(test_root/'pr02.test.mjs').read_text().replace('../.build/host/dist/index.js','@society0/host'))
shutil.copytree(test_root/'pr02',consumer/'pr02')
write(consumer/'exports.test.mjs',(test_root/'exports.test.mjs').read_text().replace('../.build/host/dist/index.js','@society0/host'))
write(consumer/'empty-boot.json','[]\n')
shutil.copytree(test_root/'native',consumer/'native-tests')
extra_tests=[]
if 'core' in release['packages']:
    shutil.copyfile(test_root/'module-sidecars.test.mjs',consumer/'module-sidecars.test.mjs')
    extra_tests.extend(['module-sidecars.test.mjs','ajv-consumer.ts','ripgrep-consumer.ts'])
for test_name,replacements in [
    ('pr03-config.test.mjs',{'../.build/host/dist/profile-loader/compiler.js':'@society0/host/profile-loader','../.build/host/dist/abi.js':'@society0/host/abi','../.build/host/dist/config-resolvers/index.js':'@society0/host/config-resolvers'}),
    ('pr04.test.mjs',{'../.build/core/dist/sqlite/database.js':'@society0/core/sqlite/database','../.build/core/dist/execution-fence/state.js':'@society0/core/execution-fence/state'}),
    ('execution-fence.test.mjs',{'../packages/core/src/execution-fence/state.ts':'@society0/core/execution-fence/state'}),
]:
    if test_name!='pr03-config.test.mjs' and 'core' not in release['packages']: continue
    text=(test_root/test_name).read_text()
    for source,target in replacements.items(): text=text.replace(source,target)
    write(consumer/test_name,text);extra_tests.append(test_name)
if 'core' in release['packages']:
    for test_name in ['pr04-cordis.test.mjs','pr04-directory.test.mjs']:
        text=(test_root/test_name).read_text()
        text=text.replace('../.build/host/dist/profile-loader/compiler.js','@society0/host/profile-loader')
        text=text.replace('../.build/core/dist/sqlite-history/history.js','@society0/core/sqlite-history')
        text=re.sub(r'\.\./\.build/(host|core)/dist/([^\"\']+)\.js',lambda match:'@society0/'+match[1]+('' if match[2]=='index' else '/'+match[2].removesuffix('/index')),text)
        assert '../.build/' not in text, test_name
        write(consumer/test_name,text);extra_tests.append(test_name)
if 'random-native' in release['packages']:
    text=(test_root/'pr11-random.test.mjs').read_text()
    text=text.replace("resolve(process.env.RANDOM_STAGE ?? '.build')", "resolve('.')")
    text=re.sub(r"new URL\('\.\./packages/random-native/([^']+)',import.meta.url\)",lambda match:"new URL('./"+match[1]+"',pathToFileURL(require.resolve('@society0/random-native/package.json')))",text)
    write(consumer/'pr11-random.test.mjs',text)
    extra_tests.append('pr11-random.test.mjs')
run(['node','--test','--test-concurrency=1','consumer.test.mjs','peer-contract.test.mjs','preflight-session.test.mjs','cli.test.mjs','pr02.test.mjs','exports.test.mjs',*extra_tests,*[str(p.relative_to(consumer)) for p in (consumer/'native-tests').glob('*.test.ts')]])
run(['node','node_modules/@society0/cli/dist/main.js','--boot','empty-boot.json'])
write(out/'result.json',{'status':'passed','node':subprocess.check_output(['node','--version'],text=True).strip(),'consumer':str(consumer),'release':release,'plugins':['@fixtures/provider','@fixtures/consumer','@fixtures/jsdoc']})
