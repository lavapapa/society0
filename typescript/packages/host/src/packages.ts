import { existsSync, readFileSync, realpathSync, statSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join, resolve, relative } from 'node:path';
import { pathToFileURL } from 'node:url';
import semver from 'semver';

interface Manifest {
  name: string;
  version: string;
  exports?: string | Record<string, unknown>;
  dependencies?: Record<string, string>;
  peerDependencies?: Record<string, string>;
  peerDependenciesMeta?: Record<string, { optional?: boolean }>;
}
interface Package { directory: string; manifest: Manifest }
function packageName(specifier: string): string {
  if (!/^(?:@[^/]+\/)?[^./][^/]*(?:\/.*)?$/.test(specifier) || specifier.includes(':')) {
    throw new Error(`BOOT_SPECIFIER: expected an installed package export, received ${specifier}`);
  }
  return specifier.startsWith('@') ? specifier.split('/').slice(0, 2).join('/') : specifier.split('/')[0]!;
}
function locate(name: string, baseURL: string): Package | undefined {
  const req = createRequire(baseURL);
  for (const path of req.resolve.paths(name) ?? []) {
    const filename = join(path, name, 'package.json');
    if (existsSync(filename)) return { directory: realpathSync(dirname(filename)), manifest: JSON.parse(readFileSync(filename, 'utf8')) };
  }
}
function required(name: string, baseURL: string): Package {
  const found = locate(name, baseURL);
  if (!found) throw new Error(`PACKAGE_MISSING: ${name} resolved from ${baseURL}`);
  return found;
}
function target(value: unknown): string | undefined {
  if (typeof value === 'string') return value;
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    for (const [condition, branch] of Object.entries(value)) {
      if (['node', 'import', 'default'].includes(condition)) {
        const result = target(branch);
        if (result) return result;
      }
    }
  }
}
function entry(pkg: Package, specifier: string): string {
  const subpath = specifier.slice(packageName(specifier).length);
  const key = subpath ? '.' + subpath : '.';
  const exports = pkg.manifest.exports;
  const value = typeof exports === 'object' && Object.keys(exports).some(k => k.startsWith('.'))
    ? exports[key] : key === '.' ? exports : undefined;
  const selected = target(value);
  if (!selected?.startsWith('./')) throw new Error(`PACKAGE_EXPORTS: ${specifier} has no explicit ESM export ${key} at ${pkg.directory}/package.json`);
  const filename = resolve(pkg.directory, selected);
  if (relative(pkg.directory, filename).startsWith('..') || !existsSync(filename) || !statSync(filename).isFile()) {
    throw new Error(`PACKAGE_EXPORTS: ${specifier} export ${key} targets missing/invalid ${filename}`);
  }
  return pathToFileURL(filename).href;
}

/** Validate the complete declared dependency graph before importing boot modules. */
export function preflight(specifiers: readonly string[], baseURL: string): string[] {
  const canonical = new Map(['@society0/host', '@society0/cordis'].map(name => [name, required(name, import.meta.url)]));
  const visited = new Set<string>();
  function inspect(pkg: Package) {
    if (visited.has(pkg.directory)) return;
    visited.add(pkg.directory);
    const { manifest, directory } = pkg;
    const expected = canonical.get(manifest.name);
    if (expected && expected.directory !== directory) {
      throw new Error(`FRAMEWORK_DUPLICATE: ${manifest.name}: host ${expected.directory}; plugin ${directory}`);
    }
    if (['@deepseek-ai/cordis', 'cordis'].includes(manifest.name)) {
      throw new Error(`FRAMEWORK_DUPLICATE: host ${canonical.get('@society0/cordis')!.directory}; foreign framework ${directory}`);
    }
    const anchor = pathToFileURL(join(directory, 'package.json')).href;
    for (const [name, expected] of canonical) {
      const resolved = locate(name, anchor);
      if (resolved && resolved.directory !== expected.directory) {
        throw new Error(`FRAMEWORK_DUPLICATE: ${name} resolved from ${directory}; host ${expected.directory}; plugin ${resolved.directory}`);
      }
    }
    for (const [name, range] of Object.entries(manifest.peerDependencies ?? {})) {
      const peer = locate(name, anchor);
      if (!peer && manifest.peerDependenciesMeta?.[name]?.optional) continue;
      if (!peer || !semver.validRange(range) || !semver.satisfies(peer.manifest.version, range)) {
        throw new Error(`PEER_CONFLICT: ${manifest.name} at ${directory} requires ${name}@${range}; resolved ${peer?.manifest.version ?? 'missing'} at ${peer?.directory ?? anchor}`);
      }
      inspect(peer);
    }
    for (const name of Object.keys(manifest.dependencies ?? {})) inspect(required(name, anchor));
  }
  const packages = specifiers.map(specifier => required(packageName(specifier), baseURL));
  // Resolve every export before any import. Entry validation deliberately accepts explicit exports only.
  const urls = packages.map((pkg, i) => entry(pkg, specifiers[i]!));
  packages.forEach((pkg) => {
    if (!pkg.manifest.peerDependencies?.['@society0/host'] && !pkg.manifest.peerDependencies?.['@society0/cordis']) {
      throw new Error(`HOST_PEER_REQUIRED: ${pkg.manifest.name} at ${pkg.directory} must declare @society0/host or @society0/cordis peer`);
    }
    inspect(pkg);
  });
  return urls;
}
