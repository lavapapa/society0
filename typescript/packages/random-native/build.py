from pathlib import Path
import subprocess, os, argparse
p=Path(__file__).resolve().parent
parser=argparse.ArgumentParser(description="Offline Random123 N-API build")
parser.add_argument('--out-dir',type=Path,default=p/'build')
a=parser.parse_args(); out=a.out_dir.resolve(); out.mkdir(parents=True,exist_ok=True)
node=Path(subprocess.check_output(['node','-p','process.execPath'],text=True).strip())
headers=Path(os.environ.get('NODE_INCLUDE_DIR',str(node.parent.parent/'include/node')))
if not (headers/'node_api.h').is_file():raise SystemExit('Node headers required; set NODE_INCLUDE_DIR')
upstream=p/'vendor/random123-726a093cd9a73f3ec3c8d7a70ff10ed8efec8d13'
cmd=[os.environ.get('CXX','g++'),'-std=c++17','-shared','-fPIC','-O2','-Wall','-Wextra','-DNAPI_VERSION=8','-I'+str(headers),'-I'+str(upstream/'include'),str(p/'source/addon.cc'),'-o',str(out/'random_native.node')]
print(' '.join(cmd),flush=True);subprocess.run(cmd,check=True)
