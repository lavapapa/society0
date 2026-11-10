import { parentPort, workerData } from 'node:worker_threads';
import { posix } from 'node:path';
import { Bash, type IFileSystem } from 'just-bash';

const port = parentPort!;
const pending = new Map<number, ReturnType<typeof Promise.withResolvers<any>>>();
let sequence = 0;

// 模块（含 just-bash）加载完成后才报告就绪；池在此之前不计命令预算。
port.on('message', async message => {
    if (message.kind === 'run') {
        try {
            const result = await execute(message.command);
            port.postMessage({ kind: 'done', result: { exitCode: result.exitCode, stdout: result.stdout, stderr: result.stderr } });
        }
        catch (error) {
            port.postMessage({ kind: 'failure', error: { message: String(error), code: (error as { code?: string })?.code } });
        }
        return;
    }
    const call = pending.get(message.id);
    if (!call) return;
    pending.delete(message.id);
    if (message.error) call.reject(Object.assign(Error(message.error.message), { code: message.error.code }));
    else call.resolve(message.value);
});

function rpc(op: string, args: unknown[]) {
    if (pending.size >= workerData.maxPendingRpc) throw Error('SHELL_RPC_PENDING');
    const id = ++sequence, call = Promise.withResolvers<any>();
    pending.set(id, call);
    port.postMessage({ id, op, args });
    return call.promise;
}
const unsupported = async () => { throw Error('SHELL_FILESYSTEM_OPERATION_UNSUPPORTED'); };
const encoding = (options: any) => typeof options === 'string' ? options : options?.encoding ?? 'utf8';
// 虚拟设备：只在本 worker 内实现，绝不落到探索会话，避免 /dev/null 成为可写落盘路径。
const DEVICES = new Set(['/dev/null', '/dev/stdout', '/dev/stderr']);
const normalise = (path: string) => posix.normalize(path);
const device = (path: string) => DEVICES.has(normalise(path));
const deviceList = (path: string) => normalise(path) === '/dev' ? ['null', 'stdout', 'stderr'] : null;
const fs: IFileSystem = {
    readFile: async (path, options) => device(path) ? '' : Buffer.from(await rpc('read', [path])).toString(encoding(options)),
    readFileBuffer: async path => device(path) ? Buffer.alloc(0) : rpc('read', [path]),
    writeFile: async (path, value, options) => { if (device(path)) return; await rpc('write', [path, Buffer.from(value as string, typeof value === 'string' ? encoding(options) : undefined).toString('base64'), false]); },
    appendFile: async (path, value, options) => { if (device(path)) return; await rpc('write', [path, Buffer.from(value as string, typeof value === 'string' ? encoding(options) : undefined).toString('base64'), true]); },
    exists: path => device(path) || deviceList(path) !== null ? Promise.resolve(true) : rpc('exists', [path]),
    stat: async path => {
        if (device(path)) return { isFile: true, isDirectory: false, isSymbolicLink: false, mode: 0o666, size: 0, mtime: new Date(0), identity: 'device:' + normalise(path) };
        if (deviceList(path)) return { isFile: false, isDirectory: true, isSymbolicLink: false, mode: 0o755, size: 0, mtime: new Date(0), identity: 'device:/dev' };
        const node = await rpc('stat', [path]);
        return { isFile: node.kind === 'file', isDirectory: node.kind === 'directory', isSymbolicLink: false, mode: node.kind === 'directory' ? 0o755 : 0o644, size: Number(node.rawBytes ?? 0), mtime: new Date(0), identity: node.objectId };
    },
    mkdir: async (path, options) => { await rpc('mkdir', [path, Boolean(options?.recursive)]); },
    readdir: path => { const list = deviceList(path); return list ? Promise.resolve(list) : rpc('list', [path]); },
    rm: async (path, options) => { await rpc('remove', [path, options ?? {}]); },
    cp: async (from, to, options) => { if (options?.recursive) throw Error('SHELL_RECURSIVE_COPY_UNSUPPORTED'); await rpc('copy', [from, to]); },
    mv: async (from, to) => { await rpc('rename', [from, to]); },
    resolvePath: (base, path) => posix.resolve(base, path),
    // just-bash 的 glob 走异步 readdir；这里不提供全量枚举（DESIGN §7.4）。
    getAllPaths: () => [],
    chmod: unsupported, symlink: unsupported, link: unsupported, readlink: unsupported,
    lstat: path => fs.stat(path),
    realpath: async path => { await fs.stat(path); return posix.resolve('/', path); },
    utimes: unsupported
};

async function execute(command: string) {
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
                const value = await rpc('command', [name, args, context.cwd]) as any;
                if (value?.fallback) {
                    if (context.origCommand) return context.origCommand(args);
                    return { exitCode: 127, stdout: '', stderr: `${name}: 不支持的选项\n` };
                }
                return { exitCode: value.exitCode, stdout: value.stdout, stderr: value.stderr ?? '' };
            }
        });
    }
    const result = await bash.exec(command);
    return { exitCode: result.exitCode, stdout: result.stdout, stderr: result.stderr };
}

// 预热：just-bash 的首次 exec 会加载命令实现。先跑一条无副作用命令，
// 让“就绪”覆盖真实执行路径，而不是只等模块 import 完成。
void execute('true').catch(() => { }).finally(() => port.postMessage({ kind: 'ready' }));
