"""授权的本机密钥只注入子进程环境；保留脱敏日志和唯一尝试目录。"""
import argparse,getpass,json,os,re,shutil,subprocess,sys
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--case',action='append',default=[])
parser.add_argument('--credential-index',type=int,default=0)
parser.add_argument('--credential-stdin',action='store_true')
args=parser.parse_args()
args.output.mkdir(parents=True,exist_ok=False)
shutil.copytree('src/society0',args.output/'source'/'society0',ignore=shutil.ignore_patterns('__pycache__'))
shutil.copytree('native/society0-filesystem/src',args.output/'source'/'native')
if args.credential_stdin:
    key=getpass.getpass('SiliconFlow credential: ')
else:
    source=Path('/Users/marvin/Library/Mobile Documents/iCloud~md~obsidian/Documents/Main/备忘/Keys.md').read_text()
    heading=re.search(r'(?m)^硅基流动\s*$',source)
    if heading is None:raise SystemExit('Authorized endpoint section unavailable')
    section='\n'.join(source[heading.end():].splitlines()[:6])
    keys=re.findall(r'\bsk-[A-Za-z0-9_-]{20,}\b',section)
    if not 0<=args.credential_index<len(keys):raise SystemExit('Authorized endpoint credential unavailable')
    key=keys[args.credential_index];del source,section,keys
request={'max_tokens':1024,'temperature':0,'parallel_tool_calls':False,'extra_body':{'enable_thinking':False},'openai_continuous_usage_stats':True}
env={**os.environ,'SOCIETY0_RUN_CORE_REAL':'1','SOCIETY0_REAL_RELEASE':'filesystem-uncommitted-candidate-20261005',
    'SOCIETY0_REAL_OUTPUT':str(args.output.resolve()),'SOCIETY0_REAL_LLM_URL':'https://api.siliconflow.cn/v1',
    'SOCIETY0_REAL_LLM_MODEL':'Qwen/Qwen3.8-27B','SOCIETY0_REAL_LLM_KEY':key,
    'SOCIETY0_REAL_EMBED_URL':'https://api.siliconflow.cn/v1','SOCIETY0_REAL_EMBED_MODEL':'Qwen/Qwen3-Embedding-0.6B',
    'SOCIETY0_REAL_EMBED_KEY':key,'SOCIETY0_REAL_EMBED_DIMENSIONS':'1024','SOCIETY0_REAL_MODEL_CAPACITY':'2',
    'SOCIETY0_REAL_REQUEST_OPTIONS':json.dumps(request)}
command=[sys.executable,'-m','pytest','-o','addopts=','-q','-x',
    *(['tests/e2e/test_core_next_real.py::'+case for case in args.case] if args.case else ['tests/e2e/test_core_next_real.py']),
    '--junitxml='+str(args.output/'real.xml')]
process=subprocess.run(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
(args.output/'real.log').write_text(process.stdout.replace(key,'[credential]'))
(args.output/'launch.json').write_text(json.dumps({'cases':args.case or 'all','credential_index':None if args.credential_stdin else args.credential_index,'credential_source':'stdin' if args.credential_stdin else 'authorized_section','exit_code':process.returncode,
    'llm_model':env['SOCIETY0_REAL_LLM_MODEL'],'embedding_model':env['SOCIETY0_REAL_EMBED_MODEL'],
    'dimensions':1024,'request_options':request},ensure_ascii=False))
print(json.dumps({'exit_code':process.returncode,'output':str(args.output),'cases':len(args.case) or 15}))
raise SystemExit(process.returncode)
