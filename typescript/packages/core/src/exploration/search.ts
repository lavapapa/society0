import { rgPath } from '@vscode/ripgrep';
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { StringDecoder } from 'node:string_decoder';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import type { ExplorationOptions } from './registry.js';
type Match = (offset: number, text: string) => void;
/** rg 进程计数：基准与度量读取（DESIGN §12.3），不参与行为。 */
export const searchProcesses = { started: 0 };
/** rg的JSON行跨stdout块解码，原文则连续流入stdin。 */
class SearchOutput {
    private decoder = new StringDecoder('utf8');
    private buffer = '';
    private bytes = 0;
    constructor(private maxBytes: number, private match: Match) { }
    push(chunk: Buffer) {
        this.bytes += chunk.length;
        if (this.bytes > this.maxBytes)
            throw Error('EXPLORATION_SEARCH_OUTPUT_BUDGET');
        this.buffer += this.decoder.write(chunk);
        let end = this.buffer.indexOf('\n');
        while (end >= 0) {
            this.event(JSON.parse(this.buffer.slice(0, end)));
            this.buffer = this.buffer.slice(end + 1);
            end = this.buffer.indexOf('\n');
        }
    }
    private event(event: any) {
        if (event.type !== 'match')
            return;
        for (const sub of event.data.submatches) {
            const text = sub.match.text ?? Buffer.from(sub.match.bytes, 'base64').toString('utf8');
            this.match(event.data.absolute_offset + sub.start, text);
        }
    }
}
/** 生命周期覆盖stdin背压、stdout消费及child close，取消等待不提前放回容量。 */
export class NativeSearch {
    private child: ChildProcessWithoutNullStreams;
    private exited: Promise<number | null>;
    private failure: unknown;
    private stderr = '';
    constructor(pattern: string, mode: 'literal' | 'regex', private options: ExplorationOptions) { searchProcesses.started++; this.child = spawn(rgPath, ['--json', '--no-config', '--regexp', pattern, ...(mode === 'literal' ? ['--fixed-strings'] : []), '--regex-size-limit', '1M', '--dfa-size-limit', '1M', '-'], { stdio: 'pipe' }); this.exited = new Promise((resolve, reject) => { this.child.once('error', reject); this.child.once('close', resolve); }); void this.exited.catch(() => { }); this.child.stderr.on('data', chunk => this.diagnostic(chunk)); }
    private stop(reason: unknown) { this.failure ??= reason; this.child.kill('SIGKILL'); }
    private diagnostic(chunk: Buffer) {
        this.stderr += chunk.toString();
        if (this.stderr.length > 4096)
            this.stop(new Error('EXPLORATION_SEARCH_OUTPUT_BUDGET'));
    }
    private async input(source: AsyncIterable<Uint8Array>) {
        try {
            await pipeline(Readable.from(source), this.child.stdin);
        }
        catch (error) {
            if ((error as NodeJS.ErrnoException).code !== 'EPIPE')
                this.stop(error);
        }
    }
    private async output(match: Match) {
        const decoder = new SearchOutput(this.options.maxSearchOutputBytes, match);
        for await (const chunk of this.child.stdout)
            decoder.push(chunk);
    }
    private result(code: number | null) {
        if (this.failure)
            throw this.failure;
        if (code !== 0 && code !== 1)
            throw Error('EXPLORATION_SEARCH ' + this.stderr);
    }
    async run(source: AsyncIterable<Uint8Array>, signal: AbortSignal, match: Match) {
        const abort = () => this.stop(signal.reason);
        signal.addEventListener('abort', abort, { once: true });
        const timer = setTimeout(() => this.stop(new Error('EXPLORATION_SEARCH_TIMEOUT')), this.options.maxSearchMs);
        const input = this.input(source);
        try {
            signal.throwIfAborted();
            await this.output(match);
            await input;
            this.result(await this.exited);
        }
        finally {
            clearTimeout(timer);
            signal.removeEventListener('abort', abort);
            if (this.child.exitCode === null)
                this.child.kill('SIGKILL');
            await input;
            await this.exited.catch(() => { });
        }
    }
}
