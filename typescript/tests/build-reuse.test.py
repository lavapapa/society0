from pathlib import Path
import subprocess
root=Path(__file__).resolve().parents[1]
before=(root/'.build/release.json').read_bytes()
result=subprocess.run(['python3','scripts/build.py','--reuse-built-dependencies'],cwd=root,text=True,capture_output=True)
assert result.returncode!=0
assert 'reuse requires an explicit package selection' in result.stderr,result.stderr
assert (root/'.build/release.json').read_bytes()==before
print('Dependency reuse requires an explicit source boundary and preserves existing release metadata on rejection')
