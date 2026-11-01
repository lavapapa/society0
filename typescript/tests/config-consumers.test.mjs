import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const repo = fileURLToPath(new URL('../', import.meta.url));
const roots = [fileURLToPath(new URL('../packages/', import.meta.url)), fileURLToPath(new URL('../apps/', import.meta.url))];
const wholeObject = new Set([
    'packages/core/src/driver-llm/index.ts#provider',
    'packages/core/src/driver-llm/index.ts#model',
    'packages/core/src/driver-llm/index.ts#reasoning',
    'packages/core/src/model-codex-subscription/index.ts#maxEventBytes',
    'packages/core/src/model-codex-subscription/index.ts#maxRawBytes',
    'packages/core/src/model-codex-subscription/index.ts#maxRequestBytes',
    'packages/core/src/model-codex-subscription/index.ts#timeoutMs',
    'packages/core/src/generation-binding/index.ts#generation',
    'packages/core/src/generation-binding/index.ts#controlSequence'
]);
const constants = new Set([
    'packages/core/src/replay/index.ts#onMissingExternalResult',
    'packages/core/src/observation/index.ts#includeRawProvider',
    'packages/host/src/run-branch/index.ts#onUnknown'
]);

function span(text, start) {
    const pairs = { '(': ')', '{': '}', '[': ']' };
    const stack = [];
    let quote;
    for (let index = start; index < text.length; index++) {
        const char = text[index];
        if (quote) {
            if (char === '\\') index++;
            else if (char === quote) quote = undefined;
        } else if (char === '"' || char === "'" || char === '`') quote = char;
        else if (pairs[char]) stack.push(pairs[char]);
        else if (char === ')' || char === '}' || char === ']') {
            stack.pop();
            if (!stack.length) return index + 1;
        }
    }
    return text.length;
}
function topKeys(objectText) {
    const keys = [];
    let depth = 0, quote, token = 1;
    for (let index = 1; index < objectText.length - 1; index++) {
        const char = objectText[index];
        if (quote) {
            if (char === '\\') index++;
            else if (char === quote) quote = undefined;
            continue;
        }
        if (char === '"' || char === "'" || char === '`') quote = char;
        else if ('([{'.includes(char)) depth++;
        else if (')]}'.includes(char)) depth--;
        else if (depth === 0 && char === ':') {
            const match = objectText.slice(token, index).match(/["']?([A-Za-z_$][\w$]*)["']?\s*$/);
            if (match) keys.push(match[1]);
        } else if (depth === 0 && char === ',') token = index + 1;
    }
    return keys;
}
function removeMetadata(text) {
    const match = /export const metadata[^=]*=\s*/.exec(text);
    if (!match) return text;
    const open = text.indexOf('{', match.index + match[0].length);
    if (open < 0) return text;
    return text.slice(0, match.index) + text.slice(span(text, open));
}
async function sources(directory) {
    const entries = await readdir(directory, { withFileTypes: true });
    const files = [];
    for (const entry of entries) {
        const path = join(directory, entry.name);
        if (entry.isDirectory()) files.push(...await sources(path));
        else if (entry.isFile() && entry.name.endsWith('.ts')) files.push(path);
    }
    return files;
}

test('RV3-17-T05 every non-const Config field has a read site', async () => {
    const files = (await Promise.all(roots.map(async root => { try { return await sources(root); } catch { return []; } }))).flat();
    const all = new Map();
    for (const file of files) all.set(file, await readFile(file, 'utf8'));
    const configs = [];
    for (const [file, text] of all) {
        const match = /export const Config\s*=\s*Schema\.object\(/.exec(text);
        if (!match) continue;
        const open = match.index + match[0].length - 1;
        const objectStart = text.indexOf('{', open);
        const objectEnd = span(text, objectStart);
        const keys = topKeys(text.slice(objectStart, objectEnd));
        const rest = removeMetadata(text.slice(0, open) + text.slice(objectEnd));
        configs.push({ file, rel: relative(repo, file).replaceAll('\\', '/'), keys, rest });
    }
    const failures = [];
    for (const item of configs) {
        const directory = dirname(item.file) + '/';
        const siblings = [...all].filter(([file]) => file !== item.file && file.startsWith(directory)).map(([, text]) => text).join('\n');
        for (const key of item.keys) {
            const pattern = new RegExp('\\b' + key + '\\b', 'g');
            const count = (item.rest.match(pattern) ?? []).length + (siblings.match(pattern) ?? []).length;
            const allowed = wholeObject.has(item.rel + '#' + key) || constants.has(item.rel + '#' + key);
            if (count === 0 && !allowed) failures.push(item.rel + '/' + key);
        }
    }
    assert.deepEqual(failures, []);
});
