from pathlib import Path
import subprocess,sys,os,json,platform,argparse,ctypes
p=Path(__file__).resolve().parent
parser=argparse.ArgumentParser(description="Offline SQLite + N-API build from bundled public source")
parser.add_argument("--addon-only",action="store_true",help="Recompile the N-API wrapper against the already built bundled library")
args=parser.parse_args()
if sys.platform!="linux" or platform.machine() not in ["x86_64","amd64"]:raise SystemExit("Supported build target: Linux x86_64/glibc")
node=Path(subprocess.check_output(["node","-p","process.execPath"],text=True).strip())
headers=Path(os.environ.get("NODE_INCLUDE_DIR",str(node.parent.parent/"include/node")))
if not (headers/"node_api.h").is_file():raise SystemExit("Node headers required; set NODE_INCLUDE_DIR to their directory")
(p/"build").mkdir(exist_ok=True)
env=os.environ.copy();env["TMPDIR"]=str(p/"build")
def compile_atomic(command,temporary,target):
 temporary=temporary.resolve();target=target.resolve()
 assert temporary.parent==target.parent and target.parent in [(p/"build").resolve(),(p/"vendor").resolve()]
 print(" ".join(command),flush=True);subprocess.run(command,env=env,check=True)
 print("Atomic artifact replacement:",temporary,"->",target,flush=True);os.replace(temporary,target)
if not args.addon_only:
 source=p/"vendor/source/sqlite3.c"
 if not source.is_file():raise SystemExit("Bundled official SQLite source is missing")
 recipe=json.loads((p/"vendor/BUILD.json").read_text())
 temporary=p/"vendor"/("libsqlite3."+str(os.getpid())+".so")
 cmd=[os.environ.get("CC","gcc"),"-std=c11","-shared","-fPIC","-O2","-pthread",*['-DSQLITE_'+value for value in recipe['definitions']],str(source),"-lm","-ldl","-o",str(temporary)]
 compile_atomic(cmd,temporary,p/"vendor/libsqlite3.so")
 library=ctypes.CDLL(str(p/"vendor/libsqlite3.so"),mode=os.RTLD_LOCAL|os.RTLD_NOW|os.RTLD_DEEPBIND)
 library.sqlite3_compileoption_get.restype=ctypes.c_char_p;library.sqlite3_sourceid.restype=ctypes.c_char_p
 info=json.loads((p/"vendor/SOURCE.json").read_text());options=[];i=0
 while value:=library.sqlite3_compileoption_get(i):options.append(value.decode());i+=1
 if library.sqlite3_sourceid().decode()!=info['sourceIdentity']:raise SystemExit("SQLite source identity differs")
 info['compileOptions']=options;info['buildRecipe']='vendor/BUILD.json';(p/"vendor/SOURCE.json").write_text(json.dumps(info,indent=2)+'\n')
temporary=p/"build"/("sqlite_native."+str(os.getpid())+".node")
cmd=[os.environ.get("CXX","g++"),"-std=c++17","-shared","-fPIC","-O2","-Wall","-Wextra","-I"+str(headers),"-I"+str(p/"vendor/source"),str(p/"native/addon.cc"),"-ldl","-o",str(temporary)]
compile_atomic(cmd,temporary,p/"build/sqlite_native.node")
