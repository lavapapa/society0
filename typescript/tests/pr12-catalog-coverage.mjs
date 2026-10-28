import {readFile} from 'node:fs/promises';
const [planPath,reportPath]=process.argv.slice(2);
if(!planPath||!reportPath)throw Error('Usage: node pr12-catalog-coverage.mjs <plan.json> <catalog-result.json>');
const plan=JSON.parse(await readFile(planPath,'utf8')),actual=JSON.parse(await readFile(reportPath,'utf8'));
const found=new Set(actual.entries.map(e=>e.package+e.exportPath.slice(1)));
const expected=new Set(plan.plugins.map(e=>e.package));
const missing=plan.plugins.filter(e=>!found.has(e.package)).map(e=>({id:e.id,specifier:e.package,ownerPR:e.pr}));
console.log(JSON.stringify({declaredCount:expected.size,observedCount:found.size,missing,additional:[...found].filter(e=>!expected.has(e)),basis:'Compare catalog entry identities only; runtime Config and metadata remain authoritative over proposed fields.'}));
if(missing.length)process.exitCode=1;
