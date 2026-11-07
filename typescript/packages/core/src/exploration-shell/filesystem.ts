import {compareCodePoints} from '@society0/host/order';
import {posix,matchesGlob} from 'node:path';import type {ExplorationSession,ExplorationNode} from '../contracts/exploration.js';import type {ActionExecutionContext} from '../contracts/domain.js';import {parseGrepArgs,parseFindArgs,matchesGrepInclude} from './argv.js';
export interface FileLimits {maxFileBytes:number;maxDirectoryEntries:number;maxRpcBytes:number;maxOutputBytes:number}
export class ExplorationFiles {
 readonly results:ExplorationNode[]=[];constructor(private session:ExplorationSession,private context:ActionExecutionContext,private limits:FileLimits){}
 private async optional(path:string){try{return await this.session.stat(path)}catch(error){if(/NOT_FOUND|NOT_VISIBLE|MISSING/.test(String(error)))return null;throw error}}
 async read(path:string){const node=await this.session.stat(path),bytes=Number(node.rawBytes);if(node.kind!=='file'||!Number.isSafeInteger(bytes)||bytes>this.limits.maxFileBytes)throw Error('SHELL_FILE_BYTES');const handle=await this.session.open(node);try{const chunks:Uint8Array[]=[];let total=0;for await(const chunk of handle.value.read({offset:'0',length:bytes,maxBytes:this.limits.maxFileBytes},this.context.signal)){total+=chunk.length;if(total>this.limits.maxFileBytes)throw Error('SHELL_FILE_BYTES');chunks.push(chunk)}return Buffer.concat(chunks)}finally{await handle.release()}}
 async write(path:string,encoded:string,append:boolean){const expected=await this.optional(path),input=Buffer.from(encoded,'base64'),bytes=append&&expected?Buffer.concat([await this.read(path),input]):input;if(bytes.length>this.limits.maxFileBytes)throw Error('SHELL_FILE_BYTES');const node=await this.session.writeFile(path,(async function*(){yield bytes})(),expected,this.context);this.results.push(node);return node}
 async list(path:string){const response=await this.session.list(path,{maxItems:this.limits.maxDirectoryEntries,maxResponseBytes:Math.min(this.limits.maxRpcBytes,262144)});if(response.next)throw Error('SHELL_DIRECTORY_ENTRIES');return response.items.map(node=>posix.basename(node.path))}
 async mkdir(path:string,recursive:boolean):Promise<void>{if(await this.optional(path)){if(recursive)return;throw Error('SHELL_PATH_EXISTS')}const parent=posix.dirname(path);if(recursive&&!await this.optional(parent))await this.mkdir(parent,true);const expectedParent=await this.session.stat(parent);await this.session.mutate({kind:'mkdir',path,expected:null,expectedParent},this.context)}
 async remove(path:string,options:{recursive?:boolean;force?:boolean}):Promise<void>{const expected=await this.optional(path);if(!expected){if(options.force)return;throw Error('SHELL_PATH_MISSING')}if(expected.kind==='directory'&&options.recursive)for(const name of await this.list(path))await this.remove(posix.join(path,name),options);await this.session.mutate({kind:'remove',path,expected},this.context)}
 async rename(from:string,to:string){await this.session.mutate({kind:'rename',from,to,expected:await this.session.stat(from),targetExpected:await this.optional(to),expectedParent:await this.session.stat(posix.dirname(to))},this.context)}
 private async command(name: string, args: string[], cwd: string) {
  if (name === 'find') return await this.findCommand(args, cwd) ?? { fallback: true };
  if (name === 'grep' || name === 'rg') return await this.grepCommand(args, cwd, name === 'rg') ?? { fallback: true };
  throw Error('SHELL_RPC_OPERATION');
 }
 private async grepCommand(args: string[], cwd: string, rg: boolean) {
  const parsed = parseGrepArgs(args, rg ? { regex: true, lineNumber: true, recursive: true } : { regex: false, lineNumber: false, recursive: false });
  if (!parsed) return null;
  if (parsed.pattern === undefined) throw Error('USAGE: grep [OPTIONS] PATTERN [PATH...]');
  const roots = (parsed.paths.length ? parsed.paths : ['.']).map(path => posix.resolve(cwd, path));
  const files = new Map<string, { count: number; lines: Set<number> }>();
  let output = '', total = 0, truncated = false;
  outer: for (const root of roots) {
   let cursor: string | undefined;
   do {
    const page = await this.session.grep({
     root, pattern: parsed.pattern, mode: parsed.flags.regex ? 'regex' : 'literal',
     ...(parsed.flags.ignoreCase ? { ignoreCase: true } : {}),
     page: { maxItems: 512, maxResponseBytes: this.limits.maxOutputBytes, cursor }
    });
    for (const match of page.items) {
     if (!matchesGrepInclude(match.path, parsed.flags.include, posix.basename(match.path))) continue;
     let entry = files.get(match.path);
     if (!entry) files.set(match.path, entry = { count: 0, lines: new Set() });
     if (entry.lines.has(match.line)) continue;
     entry.lines.add(match.line);
     entry.count++;
     total++;
     if (parsed.flags.list || parsed.flags.count) continue;
     const line = parsed.flags.lineNumber ? `${match.path}:${match.line}:${match.lineText}` : `${match.path}:${match.lineText}`;
     if (Buffer.byteLength(output) + Buffer.byteLength(line) + 1 > this.limits.maxOutputBytes) { truncated = true; break outer; }
     output += line + '\n';
    }
    cursor = page.next;
   } while (cursor);
  }
  if (parsed.flags.list) output = [...files.keys()].sort(compareCodePoints).map(path => path + '\n').join('');
  if (parsed.flags.count) output = [...files.entries()].sort((a, b) => a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0).map(([path, entry]) => `${path}:${entry.count}\n`).join('');
  if (truncated) throw Error('SHELL_OUTPUT_BYTES');
  return { exitCode: total > 0 ? 0 : 1, stdout: output, stderr: '' };
 }
 private async findCommand(args: string[], cwd: string) {
  const parsed = parseFindArgs(args);
  if (!parsed) return null;
  const root = posix.resolve(cwd, parsed.root);
  let output = '', cursor: string | undefined;
  do {
   const page = await this.session.find({ root, glob: parsed.name ?? '**', page: { maxItems: 1024, maxResponseBytes: this.limits.maxOutputBytes, cursor } });
   for (const node of page.items) {
    if (parsed.maxDepth !== undefined && this.depth(root, node.path) > parsed.maxDepth) continue;
    if (parsed.type === 'd' && node.kind !== 'directory') continue;
    if (parsed.type === 'f' && node.kind === 'directory') continue;
    if (parsed.glob !== undefined && !matchesGlob(node.path, parsed.glob) && !matchesGlob(posix.basename(node.path), parsed.glob)) continue;
    const line = node.path + '\n';
    if (Buffer.byteLength(output) + Buffer.byteLength(line) > this.limits.maxOutputBytes) throw Error('SHELL_OUTPUT_BYTES');
    output += line;
   }
   cursor = page.next;
  } while (cursor);
  return { exitCode: 0, stdout: output, stderr: '' };
 }
 private depth(root: string, path: string) {
  if (path === root) return 0;
  const relative = root === '/' ? path : path.slice(root.length);
  return relative.split('/').filter(Boolean).length;
 }
 async dispatch(op:string,args:any[]){const routes:Record<string,(...args:any[])=>Promise<unknown>>={read:(path)=>this.read(path),write:(path,data,append)=>this.write(path,data,append),exists:async(path)=>Boolean(await this.optional(path)),stat:path=>this.session.stat(path),list:path=>this.list(path),mkdir:(path,recursive)=>this.mkdir(path,recursive),remove:(path,options)=>this.remove(path,options),rename:(from,to)=>this.rename(from,to),copy:async(from,to)=>this.write(to,(await this.read(from)).toString('base64'),false),command:(name,args,cwd)=>this.command(name,args,cwd)};const route=routes[op];if(!route)throw Error('SHELL_RPC_OPERATION');return route(...args)}
}
