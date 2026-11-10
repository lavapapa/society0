import { parentPort, workerData } from 'node:worker_threads';
import { posix } from 'node:path';
import { Bash, type IFileSystem } from 'just-bash';
import { compareCodePoints } from '@society0/host/order';
import { parseGrepArgs } from './argv.js';
import { TMP_MAX_BYTES, TMP_MAX_FILES, type ShellTmpEntry, type ShellTmpState } from './tmp.js';

const port = parentPort!;
const pending = new Map<number, ReturnType<typeof Promise.withResolvers<any>>>();
let sequence = 0;

// 模块（含 just-bash）加载完成后才报告就绪；池在此之前不计命令预算。
port.on('message', async message => {
    if (message.kind === 'run') {
        loadTmp(message.tmp);
        try {
            const result = await execute(message.command);
            port.postMessage({ kind: 'done', result: { exitCode: result.exitCode, stdout: result.stdout, stderr: result.stderr, tmp: snapshotTmp() } });
        }
        catch (error) {
            port.postMessage({ kind: 'failure', error: { message: String(error), code: (error as { code?: string })?.code } });
        }
        return;
    }
    const call = pending.get(message.id);
    if (!call) return;
    pending.delete(message.id);
    if (message.error) {
        const code = message.error.code;
        call.reject(Object.assign(Error(code ? `${code}: ${message.error.message}` : message.error.message), { code }));
    }
    else call.resolve(message.value);
});

function rpc(op: string, args: unknown[]) {
    if (pending.size >= workerData.maxPendingRpc) throw Error('SHELL_RPC_PENDING');
    const id = ++sequence, call = Promise.withResolvers<any>();
    pending.set(id, call);
    port.postMessage({ id, op, args });
    void call.promise.catch(error => { const message = budgetMessage(error); if (message) budgetWarning = message; });
    return call.promise;
}
const unsupported = async () => { throw Error('SHELL_FILESYSTEM_OPERATION_UNSUPPORTED'); };
const encoding = (options: any) => typeof options === 'string' ? options : options?.encoding ?? 'utf8';
// 虚拟设备：只在本 worker 内实现，绝不落到探索会话，避免 /dev/null 成为可写落盘路径。
const DEVICES = new Set(['/dev/null', '/dev/stdout', '/dev/stderr']);
const normalise = (path: string) => posix.normalize(path);
const device = (path: string) => DEVICES.has(normalise(path));
const deviceList = (path: string) => normalise(path) === '/dev' ? ['null', 'stdout', 'stderr'] : null;

// 每次激活独立的 /tmp：状态由 ExplorationShell 按会话保管，随命令消息带进带出，
// 因此不落盘、不进 world，也不在 worker 之间或激活之间共享。
const TMP_ROOT = '/tmp';
type TmpNode = { kind: 'file'; bytes: Buffer } | { kind: 'directory' };
let tmpNodes = new Map<string, TmpNode>();
let tmpBytes = 0;
const tmpKey = (path: string) => normalise(path);
const inTmp = (path: string) => { const path2 = tmpKey(path); return path2 === TMP_ROOT || path2.startsWith(TMP_ROOT + '/'); };
const tmpCount = () => { let count = 0; for (const node of tmpNodes.values()) if (node.kind === 'file') count++; return count; };
function resetTmp() { tmpNodes = new Map([[TMP_ROOT, { kind: 'directory' }]]); tmpBytes = 0; }
resetTmp();
function snapshotTmp(): ShellTmpState {
    const entries: ShellTmpEntry[] = [];
    for (const [path, node] of [...tmpNodes.entries()].sort((a, b) => a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0)) {
        if (path === TMP_ROOT) continue;
        entries.push(node.kind === 'file' ? { path, data: node.bytes.toString('base64') } : { path, directory: true });
    }
    return { entries };
}
function loadTmp(state: ShellTmpState | undefined) {
    resetTmp();
    for (const entry of state?.entries ?? []) {
        const path = tmpKey(entry.path);
        if (!inTmp(path) || path === TMP_ROOT) continue;
        if (entry.directory) { tmpNodes.set(path, { kind: 'directory' }); continue; }
        const bytes = Buffer.from(entry.data ?? '', 'base64');
        tmpNodes.set(path, { kind: 'file', bytes });
        tmpBytes += bytes.length;
    }
}
function tmpParent(path: string) {
    const parent = posix.dirname(path), node = tmpNodes.get(parent);
    if (!node) throw Error(`ENOENT: ${parent}: No such file or directory`);
    if (node.kind !== 'directory') throw Error(`ENOTDIR: ${parent}: Not a directory`);
}
function tmpRead(path: string) {
    const key = tmpKey(path), node = tmpNodes.get(key);
    if (!node) throw Error(`ENOENT: ${key}: No such file or directory`);
    if (node.kind === 'directory') throw Error(`EISDIR: ${key}: Is a directory`);
    return node.bytes;
}
function tmpWrite(path: string, value: Uint8Array, append: boolean) {
    const key = tmpKey(path);
    if (key === TMP_ROOT) throw Error('EISDIR: /tmp: Is a directory');
    const current = tmpNodes.get(key);
    if (current?.kind === 'directory') throw Error(`EISDIR: ${key}: Is a directory`);
    tmpParent(key);
    const bytes = append && current?.kind === 'file' ? Buffer.concat([current.bytes, Buffer.from(value)]) : Buffer.from(value);
    const used = tmpBytes - (current?.kind === 'file' ? current.bytes.length : 0);
    if (used + bytes.length > TMP_MAX_BYTES) throw Error(`SHELL_TMP_BYTES: /tmp would exceed ${TMP_MAX_BYTES} bytes in this activation`);
    if (!current && tmpCount() >= TMP_MAX_FILES) throw Error(`SHELL_TMP_FILES: /tmp already holds ${TMP_MAX_FILES} files in this activation`);
    tmpNodes.set(key, { kind: 'file', bytes });
    tmpBytes = used + bytes.length;
}
function tmpStat(path: string) {
    const key = tmpKey(path), node = tmpNodes.get(key);
    if (!node) throw Error(`ENOENT: ${key}: No such file or directory`);
    return { isFile: node.kind === 'file', isDirectory: node.kind === 'directory', isSymbolicLink: false, mode: node.kind === 'directory' ? 0o755 : 0o644, size: node.kind === 'file' ? node.bytes.length : 0, mtime: new Date(0), identity: 'tmp:' + key };
}
function tmpList(path: string) {
    const key = tmpKey(path), node = tmpNodes.get(key);
    if (!node) throw Error(`ENOENT: ${key}: No such file or directory`);
    if (node.kind !== 'directory') throw Error(`ENOTDIR: ${key}: Not a directory`);
    const prefix = key === '/' ? '/' : key + '/';
    return [...tmpNodes.keys()].filter(candidate => candidate !== key && candidate.startsWith(prefix) && !candidate.slice(prefix.length).includes('/')).map(candidate => posix.basename(candidate)).sort(compareCodePoints);
}
function tmpMkdir(path: string, recursive: boolean): void {
    const key = tmpKey(path);
    if (key === TMP_ROOT) return;
    const current = tmpNodes.get(key);
    if (current) {
        if (current.kind === 'directory' && recursive) return;
        throw Error(`EEXIST: ${key}: File exists`);
    }
    const parent = posix.dirname(key);
    if (!tmpNodes.has(parent)) {
        if (!recursive) throw Error(`ENOENT: ${parent}: No such file or directory`);
        tmpMkdir(parent, true);
    }
    const parentNode = tmpNodes.get(parent)!;
    if (parentNode.kind !== 'directory') throw Error(`ENOTDIR: ${parent}: Not a directory`);
    tmpNodes.set(key, { kind: 'directory' });
}
function tmpRemove(path: string, options: { recursive?: boolean; force?: boolean }): void {
    const key = tmpKey(path);
    if (key === TMP_ROOT) throw Error('EBUSY: /tmp: Device or resource busy');
    const node = tmpNodes.get(key);
    if (!node) {
        if (options.force) return;
        throw Error(`ENOENT: ${key}: No such file or directory`);
    }
    const prefix = key + '/';
    const descendants = [...tmpNodes.keys()].filter(candidate => candidate.startsWith(prefix));
    if (node.kind === 'directory' && descendants.length && !options.recursive) throw Error(`ENOTEMPTY: ${key}: Directory not empty`);
    for (const candidate of descendants) {
        const child = tmpNodes.get(candidate)!;
        if (child.kind === 'file') tmpBytes -= child.bytes.length;
        tmpNodes.delete(candidate);
    }
    if (node.kind === 'file') tmpBytes -= node.bytes.length;
    tmpNodes.delete(key);
}
function tmpRename(from: string, to: string) {
    const source = tmpKey(from), target = tmpKey(to);
    if (source === TMP_ROOT || target === TMP_ROOT) throw Error('EBUSY: /tmp: Device or resource busy');
    const node = tmpNodes.get(source);
    if (!node) throw Error(`ENOENT: ${source}: No such file or directory`);
    const existing = tmpNodes.get(target);
    if (existing) {
        if (existing.kind === 'directory' || node.kind === 'directory') throw Error(`EEXIST: ${target}: File exists`);
        tmpBytes -= existing.bytes.length;
        tmpNodes.delete(target);
    }
    tmpParent(target);
    tmpNodes.delete(source);
    tmpNodes.set(target, node);
    if (node.kind === 'directory') {
        const prefix = source + '/';
        for (const candidate of [...tmpNodes.keys()])
            if (candidate.startsWith(prefix)) {
                const moved = tmpNodes.get(candidate)!;
                tmpNodes.delete(candidate);
                tmpNodes.set(target + candidate.slice(source.length), moved);
            }
    }
}
function tmpCopy(from: string, to: string, recursive: boolean) {
    const source = tmpKey(from), target = tmpKey(to);
    const node = tmpNodes.get(source);
    if (!node) throw Error(`ENOENT: ${source}: No such file or directory`);
    if (node.kind === 'file') { tmpWrite(target, node.bytes, false); return; }
    if (!recursive) throw Error('SHELL_RECURSIVE_COPY_UNSUPPORTED');
    tmpMkdir(target, true);
    const prefix = source + '/';
    for (const candidate of [...tmpNodes.keys()].sort(compareCodePoints))
        if (candidate.startsWith(prefix)) {
            const child = tmpNodes.get(candidate)!;
            const destination = target + candidate.slice(source.length);
            if (child.kind === 'directory') tmpMkdir(destination, true);
            else tmpWrite(destination, child.bytes, false);
        }
}
const tmpCrossMount = () => { throw Error('SHELL_TMP_CROSS_MOUNT: /tmp is an activation-local filesystem'); };

let warnings: string[] = [];
let budgetWarning: string | undefined;
function budgetMessage(error: unknown): string | undefined {
    const text = String(error), code = (error as { code?: string } | null)?.code ?? '';
    if (!/SQLITE_(QUERY|RETURN|RESULT)_BUDGET|SQLITE_RESULT_LIMIT/.test(text) && !/SQLITE_(QUERY|RETURN)_BUDGET|SQLITE_RESULT_LIMIT/.test(code))
        return undefined;
    return `结果超出单次查询预算，已截断或无法完整返回；请用 head/grep 或更具体的路径缩小范围。\n${text.split('\n')[0]}`;
}
function readBytes(path: string, value: any): Buffer {
    const bytes = Buffer.from(value.data);
    if (value.truncated)
        warnings.push(`文件 ${path} 共 ${value.total} 字节，超过单次读取上限 ${bytes.length} 字节，已截断；请用 head/grep 缩小范围。`);
    return bytes;
}
const fs: IFileSystem = {
    readFile: async (path, options) => inTmp(path) ? tmpRead(path).toString(encoding(options) as BufferEncoding) : device(path) ? '' : readBytes(path, await rpc('read', [path])).toString(encoding(options)),
    readFileBuffer: async path => inTmp(path) ? Buffer.from(tmpRead(path)) : device(path) ? Buffer.alloc(0) : readBytes(path, await rpc('read', [path])),
    writeFile: async (path, value, options) => {
        if (inTmp(path)) { tmpWrite(path, typeof value === 'string' ? Buffer.from(value, encoding(options) as BufferEncoding) : Buffer.from(value), false); return; }
        if (device(path)) return;
        await rpc('write', [path, Buffer.from(value as string, typeof value === 'string' ? encoding(options) : undefined).toString('base64'), false]);
    },
    appendFile: async (path, value, options) => {
        if (inTmp(path)) { tmpWrite(path, typeof value === 'string' ? Buffer.from(value, encoding(options) as BufferEncoding) : Buffer.from(value), true); return; }
        if (device(path)) return;
        await rpc('write', [path, Buffer.from(value as string, typeof value === 'string' ? encoding(options) : undefined).toString('base64'), true]);
    },
    exists: path => inTmp(path) ? Promise.resolve(tmpNodes.has(tmpKey(path))) : device(path) || deviceList(path) !== null ? Promise.resolve(true) : rpc('exists', [path]),
    stat: async path => {
        if (inTmp(path)) return tmpStat(path);
        if (device(path)) return { isFile: true, isDirectory: false, isSymbolicLink: false, mode: 0o666, size: 0, mtime: new Date(0), identity: 'device:' + normalise(path) };
        if (deviceList(path)) return { isFile: false, isDirectory: true, isSymbolicLink: false, mode: 0o755, size: 0, mtime: new Date(0), identity: 'device:/dev' };
        const node = await rpc('stat', [path]);
        // 超过单次读取上限的文件按可读上限报告大小，命令据此走截断读取而不是直接拒绝。
        return { isFile: node.kind === 'file', isDirectory: node.kind === 'directory', isSymbolicLink: false, mode: node.kind === 'directory' ? 0o755 : 0o644, size: Math.min(Number(node.rawBytes ?? 0), workerData.maxFileBytes), mtime: new Date(0), identity: node.objectId };
    },
    mkdir: async (path, options) => { if (inTmp(path)) { tmpMkdir(path, Boolean(options?.recursive)); return; } await rpc('mkdir', [path, Boolean(options?.recursive)]); },
    readdir: path => { if (inTmp(path)) return Promise.resolve(tmpList(path)); const list = deviceList(path); return list ? Promise.resolve(list) : rpc('list', [path]); },
    rm: async (path, options) => { if (inTmp(path)) { tmpRemove(path, options ?? {}); return; } await rpc('remove', [path, options ?? {}]); },
    cp: async (from, to, options) => {
        if (inTmp(from) || inTmp(to)) { if (!inTmp(from) || !inTmp(to)) tmpCrossMount(); tmpCopy(from, to, Boolean(options?.recursive)); return; }
        if (options?.recursive) throw Error('SHELL_RECURSIVE_COPY_UNSUPPORTED');
        await rpc('copy', [from, to]);
    },
    mv: async (from, to) => {
        if (inTmp(from) || inTmp(to)) { if (!inTmp(from) || !inTmp(to)) tmpCrossMount(); tmpRename(from, to); return; }
        await rpc('rename', [from, to]);
    },
    resolvePath: (base, path) => posix.resolve(base, path),
    // just-bash 的 glob 走异步 readdir；这里不提供全量枚举（DESIGN §7.4）。
    getAllPaths: () => [],
    chmod: unsupported, symlink: unsupported, link: unsupported, readlink: unsupported,
    lstat: path => fs.stat(path),
    realpath: async path => { if (inTmp(path)) { tmpStat(path); return tmpKey(path); } await fs.stat(path); return posix.resolve('/', path); },
    utimes: async (path) => { if (inTmp(path)) { tmpStat(path); return; } throw Error('SHELL_FILESYSTEM_OPERATION_UNSUPPORTED'); },
    createExclusive: async (path, options) => {
        if (!inTmp(path)) throw Error('SHELL_FILESYSTEM_OPERATION_UNSUPPORTED');
        const key = tmpKey(path);
        if (tmpNodes.has(key)) throw Error(`EEXIST: ${key}: File exists`);
        if (options.directory) tmpMkdir(key, false);
        else tmpWrite(key, Buffer.alloc(0), false);
    }
};

// 管道可能有非空 stdin，也可能因上游失败而为空；命令串里出现管道时，
// 没有文件参数的 grep/rg 就不要回落到探索会话的默认根。
function piped(command: string) {
    let quote: string | undefined;
    for (let index = 0; index < command.length; index++) {
        const char = command[index];
        if (quote) {
            if (char === '\\' && quote === '"') index++;
            else if (char === quote) quote = undefined;
            continue;
        }
        if (char === '\\') { index++; continue; }
        if (char === '"' || char === "'") { quote = char; continue; }
        if (char === '|' && command[index - 1] !== '|' && command[index + 1] !== '|') return true;
    }
    return false;
}

async function execute(command: string) {
    warnings = [];
    budgetWarning = undefined;
    const hasPipe = piped(command);
    const bash = new Bash({
        fs, cwd: '/workspace', env: {}, javascript: false, python: false,
        executionLimits: {
            maxSourceBytes: workerData.maxCommandBytes, maxOutputSize: workerData.maxOutputBytes,
            maxInputBytes: workerData.maxFileBytes, maxFileSystemBytes: workerData.maxFileBytes,
            maxStringLength: workerData.maxFileBytes, maxArrayElements: workerData.maxDirectoryEntries,
            maxWorkerMessageBytes: workerData.maxRpcBytes, maxExecutionTimeMs: workerData.timeoutMs,
            maxCommandCount: workerData.maxCommandCount, maxLoopIterations: 1000, maxWorkUnits: 1000000
        }
    });
    for (const name of ['grep', 'rg', 'find']) {
        bash.registerCommand({
            name, trusted: true,
            execute: async (args, context) => {
                if (name !== 'find') {
                    const parsed = parseGrepArgs(args, name === 'rg' ? { regex: true, lineNumber: true, recursive: true } : { regex: false, lineNumber: false, recursive: false });
                    // 有文件参数或 -r 时仍由会话搜索；无文件参数且 stdin 来自管道时交给原生 grep 读 stdin。
                    if (parsed && !parsed.paths.length && !parsed.flags.recursive && (String(context.stdin).length > 0 || hasPipe) && context.origCommand)
                        return context.origCommand(args);
                }
                const value = await rpc('command', [name, args, context.cwd]) as any;
                if (value?.fallback) {
                    if (context.origCommand) return context.origCommand(args);
                    return { exitCode: 127, stdout: '', stderr: `${name}: 不支持的选项\n` };
                }
                return { exitCode: value.exitCode, stdout: value.stdout, stderr: value.stderr ?? '' };
            }
        });
    }
    let result;
    try {
        result = await bash.exec(command);
    }
    catch (error) {
        const budget = budgetMessage(error);
        if (budget)
            return { exitCode: 1, stdout: '', stderr: budget + '\n' };
        throw error;
    }
    const budget = budgetMessage(result.stderr) ?? budgetWarning;
    const stderr = [budget, ...warnings].filter(Boolean).join('\n');
    return {
        exitCode: budget ? (result.exitCode || 1) : result.exitCode,
        stdout: result.stdout,
        stderr: (stderr ? stderr + '\n' : '') + (budget ? '' : result.stderr)
    };
}

// 预热：just-bash 的首次 exec 会加载命令实现。先跑一条无副作用命令，
// 让“就绪”覆盖真实执行路径，而不是只等模块 import 完成。
void execute('true').catch(() => { }).finally(() => port.postMessage({ kind: 'ready' }));
