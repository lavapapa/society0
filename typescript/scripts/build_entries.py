"""公开入口声明决定生产编译范围；入口目录包含其动态worker。"""
from pathlib import PurePosixPath

def source_pattern(target):
    if not target or not target.startswith('./dist/') or not target.endswith('.js'): return None
    source=PurePosixPath('src')/target[len('./dist/'):-3]
    if source.name=='index' and source.parent!=PurePosixPath('src'):
        return str(source.parent/'**/*.ts')
    return str(source)+'.ts'


def source_patterns(package):
    targets=[]
    for value in package.get('exports',{}).values():
        targets.append(value if isinstance(value,str) else value.get('default'))
    bins=package.get('bin',{})
    targets.extend([bins] if isinstance(bins,str) else bins.values())
    patterns=set()
    for target in targets:
        pattern=source_pattern(target)
        if pattern: patterns.add(pattern)
    assert patterns, 'production package requires declared JS exports or bins'
    return sorted(patterns)


def capture_acceptance(root,output):
    import shutil
    output.mkdir()
    shutil.copytree(root/'tests',output/'tests')


def compile_groups(folder, patterns):
    if folder != 'core': return [('all', patterns)]
    cognitive={'model-providers','prompts','driver-llm','model-replay','embedding','embedding-http','model-pi','mount-context','exploration-shell','actor-tools','agent-runtimes','agent-runtime-pi','inference-defaults','model-credentials','model-codex-subscription','exploration','mount-workspace','mount-results','mount-world','plain'}
    contracts={'src/contracts/inference.ts','src/contracts/actor-tools.ts','src/contracts/model-auth.ts'}
    selected=[path for path in patterns if path.split('/')[1] in cognitive or path in contracts]
    return [('base',[path for path in patterns if path not in selected]),('cognitive',selected)]
