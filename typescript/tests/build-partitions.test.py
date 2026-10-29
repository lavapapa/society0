from pathlib import Path
import sys,json
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'scripts'))
from build_entries import source_patterns,compile_groups
pkg=json.loads((root/'packages/core/package.json').read_text());patterns=source_patterns(pkg);groups=compile_groups('core',patterns)
assert [name for name,_ in groups]==['base','cognitive']
flat=[path for _,group in groups for path in group]
assert sorted(flat)==sorted(patterns) and len(flat)==len(set(flat))
assert 'src/model-pi/**/*.ts' in groups[1][1]
assert 'src/sqlite/**/*.ts' in groups[0][1]
assert compile_groups('host',['src/a.ts'])==[('all',['src/a.ts'])]
print('All declared roots and worker globs occur once; cognitive compiler exception is Core-only')
