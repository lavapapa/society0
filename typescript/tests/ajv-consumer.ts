import {Ajv} from 'ajv';
import assert from 'node:assert/strict';
const validate=new Ajv({allErrors:true}).compile({type:'object',properties:{text:{type:'string'}},required:['text'],additionalProperties:false});
assert.equal(validate({text:'actual schema'}),true);
assert.equal(validate({text:7}),false);
assert.equal(validate.errors?.[0]?.keyword,'type');
assert.equal(validate({text:'ok',extra:true}),false);
console.log('Ajv named class import and real JSON Schema validation passed');
