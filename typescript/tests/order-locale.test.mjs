import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const child = fileURLToPath(new URL('./order-locale-child.mjs', import.meta.url));
const locales = ['en_US.UTF-8', 'et_EE.UTF-8', 'da_DK.UTF-8'];
const expectedCollators = ['en-US', 'et-EE', 'da-DK'];

function preconditions() {
    assert.notEqual(process.config.variables.icu_small, true);
    assert.deepEqual(Intl.Collator.supportedLocalesOf(['da-DK', 'et-EE']), ['da-DK', 'et-EE']);
}

function run(mode, locale) {
    const result = spawnSync(process.execPath, [child, mode], {
        encoding: 'utf8',
        env: { ...process.env, LANG: locale, LC_ALL: locale }
    });
    assert.equal(result.status, 0, result.stderr);
    return JSON.parse(result.stdout.trim());
}

function matrix(mode) {
    preconditions();
    const outputs = locales.map(locale => run(mode, locale));
    assert.deepEqual(outputs.map(output => output.collator), expectedCollators);
    for (const output of outputs)
        assert.deepEqual(output.full, ['et', 'da']);
    assert.equal(new Set(outputs.map(output => JSON.stringify(output.locale))).size, 3);
    return outputs;
}

test('NEW-1-T03 slot order is code-point and locale independent', () => {
    const outputs = matrix('ordered');
    for (const output of outputs) {
        assert.deepEqual(output.ordered, ['B', '_x', 'a']);
        assert.deepEqual(output.cross, ['aa', 'b', 't', 'z']);
    }
});

test('NEW-1-T04 chronological and weighted ties use code-point ids', () => {
    const outputs = matrix('ranking');
    for (const output of outputs) {
        assert.deepEqual(output.chronological, ['B', '_x', 'a', 'é']);
        assert.deepEqual(output.weighted, ['B', '_x', 'a', 'é']);
        assert.deepEqual(output.crossRanking, ['aa', 'b', 't', 'z']);
    }
});
