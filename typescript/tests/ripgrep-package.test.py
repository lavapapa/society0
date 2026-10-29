from pathlib import Path
import json,subprocess,sys
root=Path(__file__).resolve().parents[1]
out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=False)
consumer=out/'consumer';consumer.mkdir();(consumer/'package.json').write_text(json.dumps({'name':'ripgrep-outside-consumer','private':True,'type':'module'}))
archives=[]
for name in ['@vscode/ripgrep','@vscode/ripgrep-linux-x64']:
    record=json.loads(subprocess.check_output(['npm','pack','--ignore-scripts','--json','--pack-destination',str(out)],cwd=root/'node_modules'/name,text=True))[0]
    archives.append(str(out/record['filename']))
subprocess.run(['npm','install','--offline','--ignore-scripts',*archives],cwd=consumer,check=True)
subprocess.run(['node','--input-type=module','-e',"import {rgPath} from '@vscode/ripgrep';import {execFileSync} from 'node:child_process';console.log(rgPath);console.log(execFileSync(rgPath,['--version'],{encoding:'utf8'}));"],cwd=consumer,check=True)
