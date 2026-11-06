import { matchesGlob } from 'node:path';

/** grep/rg 的 GNU 常用参数；遇到不支持的选项返回 null，由调用方回落到内置实现。 */
export interface GrepOptions {
    recursive: boolean;
    lineNumber: boolean;
    ignoreCase: boolean;
    regex: boolean;
    list: boolean;
    count: boolean;
    include?: string;
}
export function parseGrepArgs(args: readonly string[], defaults: { regex: boolean; lineNumber: boolean; recursive: boolean }): { pattern?: string; paths: string[]; flags: GrepOptions } | null {
    const flags: GrepOptions = { ...defaults, ignoreCase: false, list: false, count: false };
    let pattern: string | undefined, explicit = false;
    const paths: string[] = [];
    for (let index = 0; index < args.length; index++) {
        const arg = args[index]!;
        if (arg === '--') {
            for (index++; index < args.length; index++) paths.push(args[index]!);
            break;
        }
        if (arg.startsWith('--include=')) {
            flags.include = arg.slice('--include='.length);
            continue;
        }
        if (arg === '--include' || arg === '-g') {
            const value = args[++index];
            if (value === undefined) return null;
            flags.include = value;
            continue;
        }
        if (arg.startsWith('--regexp=')) {
            pattern = arg.slice('--regexp='.length);
            explicit = true;
            continue;
        }
        if (arg === '--regexp' || arg === '-e') {
            const value = args[++index];
            if (value === undefined) return null;
            pattern = value;
            explicit = true;
            continue;
        }
        if (arg === '--files-with-matches') { flags.list = true; continue; }
        if (arg === '--count') { flags.count = true; continue; }
        if (arg === '--ignore-case') { flags.ignoreCase = true; continue; }
        if (arg === '--fixed-strings') { flags.regex = false; continue; }
        if (arg.startsWith('--'))
            return null;
        if (arg.startsWith('-') && arg !== '-') {
            for (const flag of arg.slice(1)) {
                if (flag === 'r' || flag === 'R') flags.recursive = true;
                else if (flag === 'n') flags.lineNumber = true;
                else if (flag === 'i') flags.ignoreCase = true;
                else if (flag === 'E') flags.regex = true;
                else if (flag === 'F') flags.regex = false;
                else if (flag === 'l') flags.list = true;
                else if (flag === 'c') flags.count = true;
                else return null;
            }
            continue;
        }
        if (!explicit && pattern === undefined)
            pattern = arg;
        else
            paths.push(arg);
    }
    return { pattern, paths, flags };
}

/** find 的 GNU 常用表达式；不支持时返回 null。 */
export interface FindOptions {
    root: string;
    name?: string;
    glob?: string;
    type?: 'f' | 'd';
    maxDepth?: number;
}
export function parseFindArgs(args: readonly string[]): FindOptions | null {
    const options: FindOptions = { root: '.' };
    let root: string | undefined;
    for (let index = 0; index < args.length; index++) {
        const arg = args[index]!;
        if (arg === '-name' || arg === '-iname') {
            if (arg === '-iname') return null;
            const value = args[++index];
            if (value === undefined) return null;
            options.name = value;
            continue;
        }
        if (arg === '-path') {
            const value = args[++index];
            if (value === undefined) return null;
            options.glob = value;
            continue;
        }
        if (arg === '-type') {
            const value = args[++index];
            if (value !== 'f' && value !== 'd') return null;
            options.type = value;
            continue;
        }
        if (arg === '-maxdepth') {
            const value = args[++index];
            if (value === undefined || !/^(0|[1-9]\d*)$/.test(value)) return null;
            options.maxDepth = Number(value);
            continue;
        }
        if (arg === '-print') continue;
        if (arg.startsWith('-')) return null;
        if (root === undefined) { root = arg; continue; }
        // find 允许多个起点；本实现的会话 find 只有一个 root，多于一个时回落。
        return null;
    }
    options.root = root ?? '.';
    return options;
}

export function matchesGrepInclude(path: string, include: string | undefined, base: string) {
    if (include === undefined) return true;
    try { return matchesGlob(path, include) || matchesGlob(base, include); }
    catch { return true; }
}
