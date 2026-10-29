import {rgPath} from '@vscode/ripgrep';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const version=execFileSync(rgPath,['--version'],{encoding:'utf8'}).split('\n')[0];assert.match(version,/^ripgrep /);
const output=execFileSync(rgPath,['--json','--fixed-strings','actual schema',fileURLToPath(new URL('./ajv-consumer.ts',import.meta.url))],{encoding:'utf8'});
const records=output.trim().split('\n').map(line=>JSON.parse(line));assert.equal(records.filter(record=>record.type==='match').length,1);
console.log(JSON.stringify({rgPath,version,actualSearch:'passed'}));
