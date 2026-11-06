"""对已冻结的包源进行严格分区编译。"""
from pathlib import Path
import json, subprocess, os, argparse
from build_entries import source_patterns, compile_groups

def compile_phase(root, dest, folder, label, group, options):
    config=dest/('tsconfig.'+label+'.json')
    if config.exists(): config.read_text()
    config.write_text(json.dumps({'compilerOptions':options,'include':group},indent=2)+'\n')
    command=['node']
    command.extend([str(root/'node_modules/typescript/bin/tsc'),'-p',str(config)])
    print(json.dumps({'package':folder,'compilerPhase':label,'include':group,'command':command,'strict':True,'skipLibCheck':False}),flush=True)
    process=subprocess.Popen(command)
    _,status,usage=os.wait4(process.pid,0)
    process.returncode=os.waitstatus_to_exitcode(status)
    print(json.dumps({'package':folder,'compilerPhase':label,'peakRssKiB':usage.ru_maxrss,'exitCode':process.returncode}),flush=True)
    if process.returncode:raise subprocess.CalledProcessError(process.returncode,command)

def compile_package(root, dest, folder, relaxed=False, only_group=None):
    package=json.loads((dest/'package.json').read_text())
    patterns=['src/**/*.ts'] if relaxed else source_patterns(package)
    groups=compile_groups(folder,patterns)
    if only_group is not None:
        assert only_group in [name for name,_ in groups], 'unknown compiler phase'
        groups=[item for item in groups if item[0]==only_group]
    options={'target':'ES2024','module':'NodeNext','moduleResolution':'NodeNext','declaration':True,'preserveConstEnums':True,'declarationMap':False,'sourceMap':True,'inlineSources':True,'rewriteRelativeImportExtensions':True,'rootDir':'src','outDir':'dist','strict':True,'noImplicitAny':not relaxed,'noImplicitThis':not relaxed,'skipLibCheck':False,'types':['node'],'noEmitOnError':True}
    for label,group in groups:
        compile_phase(root,dest,folder,label,group,options)
    for declaration in (dest/'dist').rglob('*.d.ts'):
        text=declaration.read_text();declaration.write_text(text.replace('const enum ','enum '))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('package');parser.add_argument('--group');args=parser.parse_args()
    root=Path(__file__).resolve().parents[1];dest=Path(args.package).resolve()
    compile_package(root,dest,dest.name,only_group=args.group)
