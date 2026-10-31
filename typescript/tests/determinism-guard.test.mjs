import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const packages = new URL('../packages/', import.meta.url);
const cli = new URL('../apps/cli/src/', import.meta.url);
const rules = [
    /localeCompare|Intl\.Collator|toLocale(?:Lower|Upper)Case|\.sort\(\)|toSorted\(\)/,
    /\.(?:id|path|owner|name)\s*[<>]=?\s*(?:[a-zA-Z_$][\w$.]*|String\()/
];

async function walk(directory) {
    const files = [];
    for (const entry of await readdir(directory, { withFileTypes: true })) {
        const path = join(directory, entry.name);
        if (entry.isDirectory())
            files.push(...await walk(path));
        else if (entry.name.endsWith('.ts'))
            files.push(path);
    }
    return files;
}

test('NEW-1-T05 no locale collation, default sort or string-operator comparators', async () => {
    const files = [];
    for (const entry of await readdir(packages, { withFileTypes: true })) {
        if (!entry.isDirectory())
            continue;
        try {
            files.push(...await walk(fileURLToPath(new URL(entry.name + '/src/', packages))));
        }
        catch (error) {
            if (error.code !== 'ENOENT')
                throw error;
        }
    }
    files.push(...await walk(fileURLToPath(cli)));
    const hits = [];
    for (const file of files) {
        const lines = (await readFile(file, 'utf8')).split('\n');
        for (const [index, line] of lines.entries()) {
            for (const [rule, pattern] of rules.entries()) {
                if (pattern.test(line))
                    hits.push({ file: relative(fileURLToPath(packages), file), line: index + 1, rule, text: line.trim().slice(0, 200) });
            }
        }
    }
    assert.deepEqual(hits, []);
});
