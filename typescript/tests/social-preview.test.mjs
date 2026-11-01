import { test } from 'node:test';
import assert from 'node:assert/strict';
import { previewContent } from '../.build/social/dist/social-perception/index.js';

test('RV3-09-T02 previewContent truncates by Unicode code point and keeps short/default content', () => {
    assert.deepEqual(previewContent('short', 'p', 300), { content: 'short' });
    assert.deepEqual(previewContent('ab', 'p', 2), { content: 'ab' });
    assert.deepEqual(previewContent('a'.repeat(1000), 'p', 300), {
        content: 'a'.repeat(300), truncated: true,
        fullRef: { information: 'social/posts', input: { id: 'p' } }
    });
    assert.deepEqual(previewContent('😀x', 'p', 1), {
        content: '😀', truncated: true,
        fullRef: { information: 'social/posts', input: { id: 'p' } }
    });
    assert.deepEqual(previewContent('abc', 'p', 0), {
        content: '', truncated: true,
        fullRef: { information: 'social/posts', input: { id: 'p' } }
    });
    assert.deepEqual(previewContent('x'.repeat(1000), 'p', -1), { content: 'x'.repeat(1000) });
});
