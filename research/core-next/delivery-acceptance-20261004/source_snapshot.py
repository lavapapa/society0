"""保存本轮实际未提交源码及测试直接依赖的历史参考，身份由副本确定。"""
from pathlib import Path
import json, subprocess, sys, tarfile
root=Path(__file__).resolve().parents[3]
out=Path(__file__).resolve().parent
name=sys.argv[1]
selected=['src','tests','examples','benchmarks','docs','skill','native','tools','README.md','PROJECT.md','AGENTS.md','pyproject.toml','uv.lock','pytest.ini','LICENSE',
          'research/core-next/json-codec-baseline-f4d03a6.py','research/core-next/parity-20261004']
def keep(info):
 if any(part in {'.git','.venv','__pycache__','.pytest_cache','node_modules','target','dist','build'} for part in Path(info.name).parts):return None
 return info
with tarfile.open(out/(name+'.tar.gz'),'w:gz') as archive:
 for path in selected:
  if (root/path).exists():archive.add(root/path,arcname='source/'+path,filter=keep)
identity={'source_kind':'frozen_worktree_archive','head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
          'branch':subprocess.check_output(['git','branch','--show-current'],cwd=root,text=True).strip(),'archive':name+'.tar.gz',
          'contains_uncommitted_changes':True,'selected':selected}
(out/(name+'-identity.json')).write_text(json.dumps(identity,ensure_ascii=False,indent=2))
print(json.dumps(identity,ensure_ascii=False))
