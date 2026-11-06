from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from build_entries import source_patterns
package={'exports':{'.':{'default':'./dist/index.js'},'./sqlite':{'types':'./dist/sqlite/index.d.ts','default':'./dist/sqlite/index.js'},'./contracts/common':{'default':'./dist/contracts/common.js'},'./package.json':'./package.json'}}
assert source_patterns(package)==['src/contracts/common.ts','src/index.ts','src/sqlite/**/*.ts']
assert source_patterns({'bin':{'society0':'./dist/main.js'}})==['src/main.ts']
assert 'src/**/*.ts' not in source_patterns(package)
print('Declared ready entry directories retain workers; unrelated active directories are excluded')

from build_entries import capture_acceptance
import tempfile,os
with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as directory:
    base=Path(directory);assert base.resolve().is_relative_to(Path(os.environ['TMPDIR']).resolve())
    root=base/'source'
    for name in ['tests','examples','profiles']:(root/name).mkdir(parents=True)
    (root/'tests/fixtures').mkdir()
    (root/'tests/gate.mjs').write_text('frozen gate');(root/'tests/fixtures/input.ts').write_text('frozen fixture')
    capture_acceptance(root,base/'snapshot')
    (root/'tests/gate.mjs').write_text('next phase gate')
    assert (base/'snapshot/tests/gate.mjs').read_text()=='frozen gate'
    assert (base/'snapshot/tests/fixtures/input.ts').read_text()=='frozen fixture'
print('Acceptance test and fixture snapshots remain fixed while owners continue')
