import { readFile, writeFile } from 'node:fs/promises';

// 从已安装包的公开 metadata 生成模块锁。
// 用法：node lock.mjs [--allow-dirty] [输出文件] [包名...]
const args = process.argv.slice(2);
const allowDirty = args.includes('--allow-dirty');
const positional = args.filter(value => value !== '--allow-dirty');
const output = positional[0] ?? 'module-lock.json';
const names = positional.slice(1).length
  ? positional.slice(1)
  : ['@society0/host', '@society0/core', '@society0/extras', '@society0/social'];

const packages = {};
for (const name of names) {
  const manifestUrl = await import.meta.resolve(`${name}/package.json`);
  const manifest = JSON.parse(await readFile(new URL(manifestUrl), 'utf8'));
  const key = manifest.society?.plugin?.metadata;
  if (!key) throw new Error(`STATIC_METADATA_REQUIRED ${name}`);
  const meta = JSON.parse(await readFile(new URL(await import.meta.resolve(`${name}${key.slice(1)}`)), 'utf8'));
  if (typeof meta.source?.dirty !== 'boolean') throw new Error(`SOURCE_DIRTY_UNKNOWN ${name}`);
  if (meta.source.dirty && !allowDirty) {
    throw new Error(`DIRTY_PACKAGE ${name} built from ${meta.source.gitCommit}; pass --allow-dirty to lock it explicitly`);
  }
  packages[name] = {
    version: manifest.version,
    gitCommit: meta.source.gitCommit,
    dirty: meta.source.dirty,
    patches: meta.source.patches,
    hostAbi: meta.source.hostAbi
  };
}

await writeFile(
  output,
  JSON.stringify({ lockfile: 'installed-public-metadata', packages }, null, 2) + '\n',
  { flag: 'wx' }
);
console.log(JSON.stringify({ lockfile: output, packages: Object.keys(packages) }));
