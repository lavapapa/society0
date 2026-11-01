import { test } from 'node:test';
import assert from 'node:assert/strict';
import Schema from '../.build/schemastery/dist/index.js';
import { unknownConfigPaths } from '../.build/host/dist/config-resolvers/strict.js';

const sorted = value => [...value].sort();
const check = (schema, value, expected) => assert.deepEqual(sorted(unknownConfigPaths(schema, value)), sorted(expected));

test('RV3-17-T03 unknownConfigPaths walks object/array/tuple/dict/union/intersect', () => {
    const object = Schema.object({ known: Schema.number() });
    check(object, { known: 1 }, []);
    check(object, { known: 1, extra: 2 }, ['/extra']);
    const strict = Schema.resolve({ known: 1, extra: 2 }, object, {}, true)[0];
    assert.equal('extra' in strict, false);
    check(Schema.array(Schema.object({ id: Schema.string() })), [{ id: 'a' }], []);
    check(Schema.array(Schema.object({ id: Schema.string() })), [{ id: 'a' }, { id: 'b', x: 1 }], ['/1/x']);
    const tuple = Schema.tuple([Schema.object({ id: Schema.string() }), Schema.object({ name: Schema.string() })]);
    check(tuple, [{ id: 'a' }, { name: 'b' }], []);
    check(tuple, [{ id: 'a' }, { name: 'b' }, { id: 'c' }], ['/2']);
    const dict = Schema.dict(Schema.object({ id: Schema.string() }));
    check(dict, { a: { id: 'a' }, b: { id: 'b' } }, []);
    check(dict, { a: { id: 'a', x: 1 } }, ['/a/x']);
    const union = Schema.union([Schema.object({ id: Schema.string() }), Schema.string()]);
    check(union, { id: 'a', x: 1 }, ['/x']);
    check(union, 'literal', []);
    const intersect = Schema.intersect([Schema.object({ a: Schema.number() }), Schema.object({ b: Schema.number() })]);
    check(intersect, { a: 1, b: 2 }, []);
    check(intersect, { a: 1, b: 2, x: 3 }, ['/x']);
    check(Schema.intersect([Schema.object({ a: Schema.number() }), Schema.any()]), { a: 1, x: 2 }, []);
    check(Schema.intersect([Schema.object({ a: Schema.object({ b: Schema.number() }) })]), { a: { b: 1 } }, []);
    check(Schema.intersect([Schema.object({ a: Schema.object({ b: Schema.number() }) })]), { a: { b: 1, c: 2 } }, ['/a/c']);
    check(Schema.any(), { arbitrary: { nested: 1 } }, []);
    check(object, { known: 1, 'a/b': 2, '~': 3 }, ['/a~1b', '/~0']);
});
