"""按归属完整统计人工维护源文件；physical/nonblank 均非逻辑语句数。"""
from pathlib import Path
import json,subprocess
root=Path(__file__).resolve().parents[3]
extensions={'.py','.rs','.js','.mjs','.cjs','.ts','.tsx','.jsx','.css','.html'}
def stat(paths):
 files=sorted(set(p for p in paths if p.is_file() and p.suffix in extensions and '__pycache__' not in p.parts and 'node_modules' not in p.parts and 'target' not in p.parts))
 result=[]
 for p in files:
  lines=p.read_text().splitlines();result.append({'path':str(p.relative_to(root)),'physical':len(lines),'nonblank':sum(bool(s.strip()) for s in lines)})
 return {'files':len(result),'physical':sum(v['physical'] for v in result),'nonblank':sum(v['nonblank'] for v in result),'items':result}
paths=lambda s:list(root.glob(s))
result={'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
 'kernel':stat(paths('src/society0/kernel/**/*.py')),
 'resource_manager':stat(paths('src/society0/resource_managers.py')),
 'activation':stat(paths('src/society0/activation_pool.py')),
 'builtin_plugins':stat(paths('src/society0/plugins/**/*.py')),
 'all_src':stat(paths('src/**/*')),
 'native':stat(paths('native/**/src/**/*')),
 'all_tests':stat(paths('tests/**/*')),
 'examples':stat(paths('examples/**/*')),
 'skill_code':stat(paths('skill/**/*')),
 'ui_src':stat(paths('tools/workbench-template/src/**/*')),
 'ui_tests':stat(paths('tools/workbench-template/tests/**/*')),
 'ui_config':stat(paths('tools/workbench-template/*'))}
print(json.dumps(result,indent=2))
