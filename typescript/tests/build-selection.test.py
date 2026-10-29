from pathlib import Path
import subprocess
root=Path(__file__).resolve().parents[1]
marker=root/'.build/selection-preserved.txt';marker.parent.mkdir(exist_ok=True);marker.write_text('unrelated build result')
result=subprocess.run(['python3','scripts/build.py','--package','unknown-package'],cwd=root,text=True,capture_output=True)
assert result.returncode!=0, 'unknown build selection must reject before changing artifacts'
assert 'invalid choice' in result.stderr, result.stderr
assert marker.read_text()=='unrelated build result','selection validation removed unrelated artifacts'
print('unknown package rejected before altering build artifacts')
