// CLI run branch with a real model-pi driver: two sibling worlds branched from the same
// complete point share the source lineage and must both advance past the fork.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { call, fixture, flatten, json, skip, waitFor, worldFile } from './d7-support.mjs';

/** OpenAI-compatible SSE fixture: publish once via act, then finish with text. */
function gateway() {
    let requests = 0;
    const server = createServer(async (request, response) => {
        let body = '';
        for await (const chunk of request) body += chunk;
        requests++;
        const parsed = JSON.parse(body);
        const answered = (parsed.messages ?? []).at(-1)?.role === 'tool';
        const message = answered
            ? { role: 'assistant', content: 'posted' }
            : { role: 'assistant', content: null, tool_calls: [{ id: 'call-' + requests, type: 'function', function: { name: 'act', arguments: JSON.stringify({ action: 'social.publish', input: { content: 'sibling post ' + requests } }) } }] };
        const finish = answered ? 'stop' : 'tool_calls', usage = { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 };
        if (!parsed.stream) {
            response.writeHead(200, { 'content-type': 'application/json' });
            response.end(JSON.stringify({ id: 'c' + requests, object: 'chat.completion', created: 0, model: 'fixture-model', choices: [{ index: 0, message, finish_reason: finish }], usage }));
            return;
        }
        const chunk = value => 'data: ' + JSON.stringify({ id: 'c' + requests, object: 'chat.completion.chunk', created: 0, model: 'fixture-model', ...value }) + '\n\n';
        const delta = answered ? message : { role: 'assistant', tool_calls: message.tool_calls.map((call, index) => ({ index, ...call })) };
        response.writeHead(200, { 'content-type': 'text/event-stream' });
        response.write(chunk({ choices: [{ index: 0, delta, finish_reason: finish }] }));
        response.write(chunk({ choices: [], usage }));
        response.end('data: [DONE]\n\n');
    });
    return { server, requests: () => requests };
}

test('sibling branches from one point both resume through model activations', { skip, timeout: 900_000 }, async t => {
    const { server, requests } = gateway();
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    t.after(() => new Promise(resolve => server.close(resolve)));
    const port = server.address().port;
    const { directory, profile } = await fixture('social-sqlite', document => {
        const entries = flatten(document.patches[0].insert);
        const pi = entries.find(entry => entry.name === '@society0/core/model-pi');
        pi.config.models = pi.config.models.map(model => ({ ...model, id: 'fixture-model', baseUrl: 'http://127.0.0.1:' + port + '/v1', reasoning: false }));
        const driver = entries.find(entry => entry.name === '@society0/core/driver-llm');
        driver.config = { ...driver.config, model: 'fixture-model', maxRequests: 4, maxToolCalls: 2 };
        const round = entries.find(entry => entry.name === '@society0/social/round-robin');
        round.config = { ...round.config, rounds: 5 };
    });
    const providerId = flatten(JSON.parse(await readFile(profile, 'utf8')).patches[0].insert).find(entry => entry.name === '@society0/core/model-pi').config.providerId;
    const env = { ...process.env, S0_FIXTURE_CREDENTIALS: JSON.stringify({ [providerId]: { type: 'api_key', key: 'fixture-key' } }) };
    const credentials = ['--credentials-env', 'S0_FIXTURE_CREDENTIALS'];
    const world = await worldFile(directory);

    const source = join(directory, 'prefix');
    json(call(['run', 'start', '--profile', profile, '--dir', source, '--world', world, '--ticks', '1', ...credentials, '--detach', '--json'], { env }));
    const prefix = await waitFor(source, value => value.state === 'completed' || value.state === 'failed' || value.state === 'paused');
    assert.equal(prefix.state, 'completed', JSON.stringify(prefix));
    const prefixRequests = requests();
    assert.ok(prefixRequests > 0, 'prefix must reach the model gateway');

    const worlds = ['world-a', 'world-b'].map(name => join(directory, name));
    for (const dir of worlds) assert.equal(json(call(['run', 'branch', '--dir', source, '--target-dir', dir, '--json'])).state, 'paused');
    const lineage = JSON.parse(await readFile(join(source, 'entry-status.json'), 'utf8')).lineage;
    for (const dir of worlds) assert.equal(JSON.parse(await readFile(join(dir, 'entry-status.json'), 'utf8')).lineage, lineage);

    for (const dir of worlds) {
        json(call(['run', 'resume', '--dir', dir, '--ticks', '2', ...credentials, '--detach', '--json'], { env }));
        const status = await waitFor(dir, value => value.state === 'completed' || value.state === 'failed' || value.state === 'paused');
        const result = call(['run', 'result', '--dir', dir, '--json']);
        assert.equal(status.state, 'completed', dir + ' ' + JSON.stringify(status) + ' ' + result.stdout);
        assert.equal(status.completedTicks, 2, JSON.stringify(status));
        const report = JSON.parse(result.stdout);
        assert.equal(report.activations.failed, 0, JSON.stringify(report.activations));
        assert.doesNotMatch(result.stdout, /persistence_error|MODEL_ATTEMPT_EXISTING/);
    }
    assert.ok(requests() > prefixRequests, 'both siblings must send their own model requests');
});
