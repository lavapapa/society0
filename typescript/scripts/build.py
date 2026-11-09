"""构建真实cohort与生产包；--package选择依赖闭包并保留无关产物。"""
from pathlib import Path
import argparse, json, shutil, subprocess, sys
from build_entries import capture_acceptance
from build_compile import compile_package
from build_native import assemble_native, verify_native, copy_inputs
from build_identity import worktree_identity, validate_release, save_worktree_evidence, validate_dependency
root=Path(__file__).resolve().parents[1]
build=root/'.build'
cohort=['cosmokit','schemastery','cordis','loader','include','group']
native=['sqlite-native','random-native']
order=[*cohort,*native,'host','core','extras','social','cli']
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--package',action='append',choices=order,help='可重复指定；省略时构建全部生产包')
parser.add_argument('--reuse-built-dependencies',action='store_true',help='仅读取显式选择包的源码，复用已构建依赖')
parser.add_argument('--release',action='store_true',help='发行构建：要求-B、干净提交、无忽略输入、全量、不复用')
args=parser.parse_args()
selected=set(args.package or order)
if 'social' in selected: selected.add('extras')
if 'extras' in selected: selected.add('core')
if 'core' in selected: selected.update(['host',*native])
if 'cli' in selected: selected.update(['host','core','extras','social'])
if selected-set(native): selected.update(cohort)
manifest=json.loads((root/'vendor/source-manifest.json').read_text())
series=json.loads((root/'patches/series.json').read_text())
assert manifest==json.loads((root/'vendor-baseline/source-manifest.json').read_text()), 'source identity differs from original cohort'
assert manifest['repository']=='https://github.com/deepseek-ai/deepseek-harness' and manifest['commit']==series['upstreamCommit'], 'source identity missing or mismatched'
assert series.get('patches'), 'ordered patch series missing'
for i,item in enumerate(series['patches'],1):
    assert item['order']==i and item['purpose'] and (root/'patches'/item['file']).is_file(), 'invalid patch order/source'
assert (root/'package-lock.json').is_file(), 'dependency lock missing'
worktree=worktree_identity(root)
if args.release: validate_release(worktree,args.package,args.reuse_built_dependencies,sys.flags.dont_write_bytecode)
reused=set()
if args.reuse_built_dependencies:
    assert args.package, 'reuse requires an explicit package selection'
    requested=set(args.package)
    reused=selected-requested
    for folder in reused:
        package=json.loads((build/folder/'package.json').read_text())
        provenance=json.loads((build/folder/'SOURCE.json').read_text())
        validate_dependency(package,provenance,folder,manifest['commit'],worktree)
        if folder in native: verify_native(build,folder)
    selected=requested
assert build.resolve()==root/'.build'
build.mkdir(exist_ok=True)
save_worktree_evidence(build,worktree)
def reset(folder):
    target=build/folder
    assert folder in [*order,'vendor','acceptance'] and target.resolve()==build/folder, 'unexpected build target'
    if target.exists(): shutil.rmtree(target)
def link(name,target):
    path=build/'node_modules'/name
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.is_symlink():
        assert path.absolute().is_relative_to(build/'node_modules')
        path.unlink()
    else: assert not path.exists(), f'build dependency must be a symlink: {path}'
    path.symlink_to(target,target_is_directory=True)
for folder in reused: link('@society0/'+folder,build/folder)
reset('vendor')
shutil.copytree(root/'vendor-baseline',build/'vendor')
for item in series['patches']:
    subprocess.run(['patch','--batch','-p1','-i',str(root/'patches'/item['file'])],cwd=build,check=True)
for package in manifest['packages']:
    for file in (build/'vendor'/package['directory']).rglob('*'):
        if file.is_file(): assert file.read_bytes()==(root/'vendor'/file.relative_to(build/'vendor')).read_bytes(), f'vendor differs from ordered patches: {file}'
reset('acceptance')
capture_acceptance(root,build/'acceptance')
names={package['package']:'@society0/'+package['directory'] for package in manifest['packages']}
versions={names[package['package']]:package['source_manifest_version']+'-society0.1' for package in manifest['packages']}
source={'repository':manifest['repository'],'commit':manifest['commit'],'license':manifest['license'],'packages':[{key:package[key] for key in ['directory','package','source_manifest_version']} for package in manifest['packages']], 'patches':series['patches'],'distributionTransform':'Map @deepseek-ai cohort imports and augmentation targets to @society0; tsc rewrites relative .ts imports to .js, preserves runtime enums and removes const from emitted enum declarations; no runtime hooks.', 'dependencyLock':'BUILD-LOCK.json'}
for name in ['@standard-schema/spec','js-yaml','argparse','p-limit','yocto-queue','resolve.exports','ajv','fast-deep-equal','fast-uri','json-schema-traverse','require-from-string','@vscode/ripgrep','@vscode/ripgrep-linux-x64','@earendil-works/pi-ai','@earendil-works/pi-agent-core','just-bash','@modelcontextprotocol/sdk']:
    link(name,root/'node_modules'/name)
for folder in cohort:
    if folder not in selected: continue
    reset(folder)
    dest=build/folder
    shutil.copytree(build/'vendor'/folder,dest)
    package=json.loads((dest/'upstream-package.json').read_text())
    package['name']=names[package['name']]; package['version']=versions[package['name']]
    for key in ['dependencies','peerDependencies']:
        package[key]={names.get(name,name):versions[names[name]] if name in names else version for name,version in package.get(key,{}).items()}
    package['peerDependenciesMeta']={names.get(name,name):value for name,value in package.get('peerDependenciesMeta',{}).items()}
    for key in ['bin','module','publishConfig']: package.pop(key,None)
    package.update(main='./dist/index.js',types='./dist/index.d.ts',exports={'.':{'types':'./dist/index.d.ts','default':'./dist/index.js'},'./package.json':'./package.json'},files=['dist','LICENSE','SOURCE.json','BUILD-LOCK.json','PATCHES','upstream-package.json'],engines={'node':'>=24.16.0'})
    for file in (dest/'src').rglob('*.ts'):
        text=file.read_text()
        for old,new in names.items(): text=text.replace("'"+old+"'","'"+new+"'").replace('"'+old+'"','"'+new+'"')
        file.write_text(text)
    (dest/'package.json').write_text(json.dumps(package,indent=2)+'\n')
    link(package['name'],dest)
for folder,original in [('sqlite-native','packages/sqlite-native'),('random-native','packages/random-native'),('host','packages/host'),('core','packages/core'),('extras','packages/extras'),('social','packages/social'),('cli','apps/cli')]:
    if folder not in selected: continue
    reset(folder)
    dest=build/folder
    if folder in native:
        assemble_native(root,build,folder,original)
    else:
        dest.mkdir()
        copy_inputs(root,original,['.'],dest)
        shutil.copyfile(root.parent/'LICENSE',dest/'LICENSE')
    link('@society0/'+folder,dest)
for folder in order:
    if folder not in selected: continue
    dest=build/folder
    package=json.loads((dest/'package.json').read_text())
    package['files']=list(dict.fromkeys(package['files']+['SOURCE.json','BUILD-LOCK.json','PATCHES']))
    (dest/'package.json').write_text(json.dumps(package,indent=2)+'\n')
    shutil.copyfile(root/'package-lock.json',dest/'BUILD-LOCK.json')
    shutil.copytree(root/'patches',dest/'PATCHES')
    identity=dict(source)
    identity['implementation']={'package':package['name'],'version':package['version'],'gitCommit':worktree['gitCommit'],'dirty':worktree['dirty'],'release':args.release,'lockfile':'BUILD-LOCK.json','patches':[item['file'] for item in series['patches']]}
    if worktree['dirty']: identity['implementation']['dirtyPaths']=worktree['dirtyPaths']
    if folder in native: identity['native']={'source':json.loads((dest/'vendor/SOURCE.json').read_text()),'build':json.loads((dest/'NATIVE-BUILD.json').read_text())}
    (dest/'SOURCE.json').write_text(json.dumps(identity,indent=2)+'\n')
    if folder in native:
        print('assembled native',folder,flush=True)
        continue
    compile_package(root,dest,folder,relaxed=folder in cohort)
    if folder in ['host','core','extras','social']:
        subprocess.run(['node',str(root/'scripts/sidecars.mjs'),str(root),str(dest)],check=True)
    print('compiled',folder,flush=True)
(build/'release.json').write_text(json.dumps({'source':source,'implementation':{key:worktree[key] for key in ['gitCommit','dirty']},'release':args.release,'implementationEvidence':'worktree-evidence/identity.json','packages':[folder for folder in order if folder in selected],'selection':args.package or ['all'],'reusedDependencies':[folder for folder in order if folder in reused]},indent=2)+'\n')

if args.release:
    after=worktree_identity(root)
    assert after==worktree, 'RELEASE_BUILD_MUTATED_SOURCE '+json.dumps({name:{key:value[key] for key in ['gitCommit','dirty','dirtyPaths','ignoredInputs']} for name,value in [('before',worktree),('after',after)]})
