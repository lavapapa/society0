// 传入独立 npm 安装目录中的 ESM 入口；无模型、无项目依赖修改。
import {pathToFileURL} from 'node:url';
const {Bash} = await import(pathToFileURL(process.argv[2]));
const size = 8 * 1024 * 1024;
let calls = 0;
const bash = new Bash({files:{'/large':() => {calls++;return 'x\n'.repeat(size/2)}}});
const results=[];
for(const script of ['head -c 4 /large','tail -c 4 /large']) {
  const r=await bash.exec(script);
  if(r.exitCode!==0 || r.stdout!=='x\nx\n')throw new Error(JSON.stringify(r));
  results.push({script,exit_code:r.exitCode,output_bytes:Buffer.byteLength(r.stdout)});
}
if(calls!==1)throw new Error(`unexpected lazy calls ${calls}`);
console.log(JSON.stringify({node:process.version,just_bash:'3.6.0',source_bytes:size,lazy_calls:calls,source_bytes_materialized:size*calls,results},null,2));
