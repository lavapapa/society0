"""从保留的六包源码生成独立包；所有目录操作限制在 .build。"""
from pathlib import Path
import json, shutil, subprocess
root=Path(__file__).resolve().parents[1]
build=root/'.build'
manifest=json.loads((root/'vendor/source-manifest.json').read_text())
series=json.loads((root/'patches/series.json').read_text())
assert manifest==json.loads((root/'vendor-baseline/source-manifest.json').read_text()), 'source identity differs from original cohort'
assert manifest['repository']=='https://github.com/deepseek-ai/deepseek-harness' and manifest['commit']==series['upstreamCommit'], 'source identity missing or mismatched'
assert series.get('patches'), 'ordered patch series missing'
for i,p in enumerate(series['patches'],1):
    assert p['order']==i and p['purpose'] and (root/'patches'/p['file']).is_file(), 'invalid patch order/source'
assert (root/'package-lock.json').is_file(), 'dependency lock missing'
assert build.resolve()==root/'.build'
if build.exists(): shutil.rmtree(build)
build.mkdir()
names={p['package']: '@society0/'+{'loader':'loader','include':'include','group':'group'}.get(p['directory'],p['directory']) for p in manifest['packages']}
versions={names[p['package']]:p['source_manifest_version']+'-society0.1' for p in manifest['packages']}
shutil.copytree(root/'vendor-baseline',build/'vendor')
for p in series['patches']:
    subprocess.run(['patch','--batch','-p1','-i',str(root/'patches'/p['file'])],cwd=build,check=True)
for p in manifest['packages']:
    for f in (build/'vendor'/p['directory']).rglob('*'):
        if f.is_file(): assert f.read_bytes()==(root/'vendor'/f.relative_to(build/'vendor')).read_bytes(), f'vendor differs from ordered patches: {f}'
    shutil.copytree(build/'vendor'/p['directory'],build/p['directory'])
source={'repository':manifest['repository'],'commit':manifest['commit'],'license':manifest['license'],'packages':[{k:p[k] for k in ['directory','package','source_manifest_version']} for p in manifest['packages']], 'patches':series['patches'],'distributionTransform':'Map @deepseek-ai cohort imports and augmentation targets to @society0; tsc rewrites relative .ts imports to .js, preserves runtime enums and removes const from emitted enum declarations; no runtime hooks.', 'dependencyLock':'BUILD-LOCK.json'}
(build/'node_modules/@society0').mkdir(parents=True)
# 构建依赖来自本项目锁文件。
lab=root/'node_modules'
for name in ['@standard-schema/spec','js-yaml','argparse']:
    dest=build/'node_modules'/name; dest.parent.mkdir(parents=True,exist_ok=True); dest.symlink_to(lab/name,target_is_directory=True)
for folder in ['cosmokit','schemastery','cordis','loader','include','group']:
    dest=build/folder; package=json.loads((dest/'upstream-package.json').read_text())
    package['name']=names[package['name']]; package['version']=versions[package['name']]
    for key in ['dependencies','peerDependencies']:
        package[key]={names.get(k,k):versions[names[k]] if k in names else v for k,v in package.get(key,{}).items()}
    package['peerDependenciesMeta']={names.get(k,k):v for k,v in package.get('peerDependenciesMeta',{}).items()}
    for key in ['bin','module','publishConfig']: package.pop(key,None)
    package.update(main='./dist/index.js',types='./dist/index.d.ts',exports={'.':{'types':'./dist/index.d.ts','default':'./dist/index.js'},'./package.json':'./package.json'},files=['dist','LICENSE','SOURCE.json','BUILD-LOCK.json','PATCHES','upstream-package.json'],engines={'node':'>=24.16.0'})
    for f in (dest/'src').rglob('*.ts'):
        s=f.read_text()
        for old,new in names.items(): s=s.replace("'"+old+"'","'"+new+"'").replace('"'+old+'"','"'+new+'"')
        f.write_text(s)
    (dest/'package.json').write_text(json.dumps(package,indent=2)+'\n')
    (build/'node_modules'/package['name']).symlink_to(dest,target_is_directory=True)
for folder,original in [('host','packages/host'),('cli','apps/cli')]:
    shutil.copytree(root/original,build/folder)
    shutil.copyfile(root.parent/'LICENSE',build/folder/'LICENSE')
    (build/'node_modules/@society0'/folder).symlink_to(build/folder,target_is_directory=True)
for folder in ['cosmokit','schemastery','cordis','loader','include','group','host','cli']:
    dest=build/folder
    package=json.loads((dest/'package.json').read_text())
    package['files']=list(dict.fromkeys(package['files']+['BUILD-LOCK.json','PATCHES']))
    (dest/'package.json').write_text(json.dumps(package,indent=2)+'\n')
    shutil.copyfile(root/'package-lock.json',dest/'BUILD-LOCK.json')
    shutil.copytree(root/'patches',dest/'PATCHES')
    (dest/'SOURCE.json').write_text(json.dumps(source,indent=2)+'\n')
    options={'target':'ES2024','module':'NodeNext','moduleResolution':'NodeNext','declaration':True,'preserveConstEnums':True,'declarationMap':False,'sourceMap':False,'rewriteRelativeImportExtensions':True,'rootDir':'src','outDir':'dist','strict':True,'noImplicitAny':folder in ['host','cli'],'noImplicitThis':folder in ['host','cli'],'skipLibCheck':False,'types':['node'],'noEmitOnError':True}
    (dest/'tsconfig.json').write_text(json.dumps({'compilerOptions':options,'include':['src/**/*.ts']},indent=2))
    subprocess.run(['node',str(root/'node_modules/typescript/bin/tsc'),'-p',str(dest/'tsconfig.json')],check=True)
    for declaration in (dest/'dist').rglob('*.d.ts'):
        declaration.write_text(declaration.read_text().replace('const enum ', 'enum '))
    print('compiled',folder,flush=True)
(build/'release.json').write_text(json.dumps({'source':source,'packages':['cosmokit','schemastery','cordis','loader','include','group','host','cli']},indent=2)+'\n')
