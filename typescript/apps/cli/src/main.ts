#!/usr/bin/env node
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { createHost, type BootEntry } from '@society0/host';
import type {} from '@society0/host/commands';
import { checkProfile, generateLock, PlatformError, runResult, runStatus, startRun, worker, ERROR_SCHEMA, type CheckReport, type StartOptions } from './platform.js';

interface Invocation { boot: string; command?: string; input: unknown }
type PlatformInvocation =
    | { kind: 'check'; profile: string; lock?: string; patches?: string; base?: string; credentialsEnv?: string }
    | { kind: 'lock'; base: string; out: string; packages: string[] }
    | { kind: 'start'; options: StartOptions }
    | { kind: 'resume'; options: StartOptions }
    | { kind: 'status'; dir: string }
    | { kind: 'result'; dir: string }
    | { kind: 'worker'; options: StartOptions };

const LEGACY_USAGE = 'Usage: society0 --boot <boot.json> [--command <name> [--input <json>]]';
const PLATFORM_USAGE = [
    'Usage: society0 <command> [options]',
    '',
    'Commands:',
    '  check --profile <p> [--lock <f>] [--patches <f>] [--base <d>] [--credentials-env <NAME>] --json',
    '  lock --base <d> --out <lock.json> <package>... --json',
    '  run start --profile <p> --dir <run> [--lineage <d>] [--ticks N] [--world <f>] [--base <d>] [--credentials-env <NAME>] --detach --json',
    '  run resume --dir <run> [--credentials-env <NAME>] --detach --json',
    '  run status --dir <run> --json',
    '  run result --dir <run> --json',
    '',
    'Options:',
    '  --boot <boot.json>  Explicit BootEntry array; package paths resolve from this file.',
    '  --command <name>    Execute a command registered by the boot plugins.',
    '  --input <json>      Command input; defaults to {}.',
    '  --json              Emit versioned JSON.',
    '  --help, -h          Show this help and exit.'
].join('\n');

function usage(): number {
    console.error(LEGACY_USAGE);
    console.error('Run society0 --help for the platform entry commands.');
    return 2;
}

function parseLegacy(argv: string[]): Invocation | number {
    let values;
    try { ({ values } = parseArgs({ args: argv, options: { boot: { type: 'string' }, command: { type: 'string' }, input: { type: 'string' }, help: { type: 'boolean', short: 'h' } } })); }
    catch { return usage(); }
    if (values.help) {
        console.log(PLATFORM_USAGE + '\n\n' + LEGACY_USAGE + '\n  --boot <boot.json>  Explicit BootEntry array; package paths resolve from this file.\n  --command <name>    Execute a command registered by the boot plugins.\n  --input <json>      Command input; defaults to {}.');
        return 0;
    }
    if (!values.boot || (values.input !== undefined && !values.command)) return usage();
    const input: unknown = values.input === undefined ? {} : JSON.parse(values.input);
    return { boot: values.boot, command: values.command, input };
}

function absolute(value: string | undefined): string | undefined {
    return value === undefined ? undefined : resolve(value);
}

function startOptions(values: Record<string, string | boolean | undefined>, mode: 'start' | 'resume'): StartOptions {
    const ticks = typeof values.ticks === 'string' ? Number(values.ticks) : undefined;
    return {
        profile: values.profile as string,
        dir: values.dir as string,
        lineage: values.lineage as string | undefined,
        lock: values.lock as string | undefined,
        patches: values.patches as string | undefined,
        base: values.base as string | undefined,
        world: values.world as string | undefined,
        credentialsEnv: values['credentials-env'] as string | undefined,
        ticks,
        detach: values.detach === true,
        mode
    };
}

function parsePlatform(argv: string[]): PlatformInvocation | number {
    const [command, subcommand] = argv;
    if (command === 'check') {
        let values;
        try {
            ({ values } = parseArgs({ args: argv.slice(1), options: { profile: { type: 'string' }, lock: { type: 'string' }, patches: { type: 'string' }, base: { type: 'string' }, 'credentials-env': { type: 'string' }, json: { type: 'boolean' }, help: { type: 'boolean', short: 'h' } } }));
        }
        catch { return usage(); }
        if (values.help) { console.log(PLATFORM_USAGE); return 0; }
        if (!values.profile) return usage();
        return { kind: 'check', profile: resolve(values.profile), lock: absolute(values.lock), patches: absolute(values.patches), base: values.base ? resolve(values.base) : undefined, credentialsEnv: values['credentials-env'] };
    }
    if (command === 'lock') {
        let values, positionals;
        try {
            ({ values, positionals } = parseArgs({ args: argv.slice(1), allowPositionals: true, options: { base: { type: 'string' }, out: { type: 'string' }, json: { type: 'boolean' }, help: { type: 'boolean', short: 'h' } } }));
        }
        catch { return usage(); }
        if (values.help) { console.log(PLATFORM_USAGE); return 0; }
        if (!values.base || !values.out || !positionals.length) return usage();
        return { kind: 'lock', base: resolve(values.base), out: resolve(values.out), packages: positionals };
    }
    if (command === '__worker') {
        let values;
        try {
            ({ values } = parseArgs({ args: argv.slice(1), options: { mode: { type: 'string' }, profile: { type: 'string' }, dir: { type: 'string' }, lineage: { type: 'string' }, lock: { type: 'string' }, patches: { type: 'string' }, base: { type: 'string' }, world: { type: 'string' }, 'credentials-env': { type: 'string' }, ticks: { type: 'string' } } }));
        }
        catch { return usage(); }
        if (!values.profile || !values.dir || (values.mode !== 'start' && values.mode !== 'resume')) return usage();
        return { kind: 'worker', options: startOptions(values as never, values.mode) };
    }
    if (command === 'run' && subcommand) {
        let values;
        try {
            ({ values } = parseArgs({ args: argv.slice(2), options: { profile: { type: 'string' }, dir: { type: 'string' }, lineage: { type: 'string' }, lock: { type: 'string' }, patches: { type: 'string' }, base: { type: 'string' }, world: { type: 'string' }, 'credentials-env': { type: 'string' }, ticks: { type: 'string' }, detach: { type: 'boolean' }, json: { type: 'boolean' }, help: { type: 'boolean', short: 'h' } } }));
        }
        catch { return usage(); }
        if (values.help) { console.log(PLATFORM_USAGE); return 0; }
        if (subcommand === 'status' || subcommand === 'result') {
            if (!values.dir) return usage();
            return { kind: subcommand, dir: resolve(values.dir) };
        }
        if (subcommand === 'start') {
            if (!values.profile || !values.dir) return usage();
            return { kind: 'start', options: startOptions(values as never, 'start') };
        }
        if (subcommand === 'resume') {
            if (!values.dir) return usage();
            return { kind: 'resume', options: startOptions({ ...values, profile: values.profile ?? '', dir: values.dir } as never, 'resume') };
        }
        return usage();
    }
    return usage();
}

function errorJson(error: unknown): string {
    if (error instanceof PlatformError)
        return JSON.stringify({ schema: ERROR_SCHEMA, code: error.code, message: error.message, hint: error.hint });
    const message = error instanceof Error ? error.message : String(error);
    const match = /^([A-Z][A-Z0-9_]+)/.exec(message);
    return JSON.stringify({ schema: ERROR_SCHEMA, code: match?.[1] ?? 'PLATFORM_FAILED', message, hint: '检查命令参数、profile 与 run 目录；详见 typescript/docs/platform-entry.md' });
}

function printJson(value: unknown): void { console.log(JSON.stringify(value)); }

async function runPlatform(invocation: PlatformInvocation): Promise<number> {
    switch (invocation.kind) {
        case 'check': {
            const report: CheckReport = await checkProfile(invocation);
            printJson(report);
            return report.ok ? 0 : 2;
        }
        case 'lock': {
            printJson(await generateLock(invocation));
            return 0;
        }
        case 'status': {
            printJson(await runStatus(invocation.dir));
            return 0;
        }
        case 'result': {
            printJson(await runResult(invocation.dir));
            return 0;
        }
        case 'start':
        case 'resume': {
            const options = invocation.options;
            if (!options.detach) {
                const code = await worker(options);
                printJson(await runStatus(options.dir));
                return code;
            }
            printJson(await startRun(fileURLToPath(import.meta.url), options));
            return 0;
        }
        case 'worker': {
            return await worker(invocation.options);
        }
    }
}

async function legacy(invocation: Invocation): Promise<number> {
    const { input } = invocation;
    const url = pathToFileURL(resolve(invocation.boot));
    const boot: readonly BootEntry[] = JSON.parse(await readFile(url, 'utf8'));
    const host = await createHost({ boot, baseURL: url.href });
    const requested = Promise.withResolvers<void>();
    const controller = new AbortController();
    let signalCode = 0;
    let closing: ReturnType<typeof host.close> | undefined;
    const close = () => closing ??= host.close();
    const shutdown = () => { requested.resolve(); };
    const interrupt = () => { signalCode=130; controller.abort(new DOMException('SIGINT','AbortError')); shutdown(); };
    const terminate = () => { signalCode=143; controller.abort(new DOMException('SIGTERM','AbortError')); shutdown(); };
    process.once('SIGINT', interrupt);
    process.once('SIGTERM', terminate);
    process.once('beforeExit', shutdown);
    try {
        if (invocation.command) {
            const commands=host.ctx.get('commands');
            if (!commands) throw Error('COMMANDS_NOT_AVAILABLE: --boot must install @society0/host/commands');
            const execution=commands.execute(invocation.command,input,{kind:'host',requestId:`cli:${process.pid}`,signal:controller.signal});
            const result=await execution;
            await close();
            if (signalCode) return signalCode;
            console.log(JSON.stringify(result ?? null));
            return 0;
        }
        await requested.promise;
        await close();
        return 0;
    } finally {
        try { await close(); }
        finally {
            process.off('SIGINT', interrupt);
            process.off('SIGTERM', terminate);
            process.off('beforeExit', shutdown);
        }
    }
}

async function main(argv = process.argv.slice(2)): Promise<number> {
    if (argv.includes('--help') || argv.includes('-h') || argv.length === 0) {
        if (argv.length === 0) return usage();
    }
    const legacyInvocation = argv[0]?.startsWith('--') ? parseLegacy(argv) : undefined;
    if (legacyInvocation !== undefined)
        return typeof legacyInvocation === 'number' ? legacyInvocation : await legacy(legacyInvocation);
    const parsed = parsePlatform(argv);
    if (typeof parsed === 'number')
        return parsed;
    try {
        return await runPlatform(parsed);
    }
    catch (error) {
        console.error(errorJson(error));
        return 1;
    }
}
// Keep cleanup in the module's awaited lifetime so unresolved cleanup retains Node's exit 13.
try {
    process.exitCode = await main();
} catch (error) {
    console.error(errorJson(error));
    process.exitCode = 1;
}
