import { existsSync, readFileSync, realpathSync, statSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join, resolve, relative } from 'node:path';
import { pathToFileURL } from 'node:url';
import semver from 'semver';
import { exports as resolveExports } from 'resolve.exports';

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
function entry(pkg: Package, specifier: string): string {
  const subpath = specifier.slice(packageName(specifier).length);
  const key = subpath ? '.' + subpath : '.';
  let selected: string | undefined;
  try { selected = resolveExports(pkg.manifest, key)?.[0]; }
  catch (cause) { throw new Error(`PACKAGE_EXPORTS: ${specifier} export ${key} at ${pkg.directory}/package.json`, {cause}); }
  if (!selected?.startsWith('./')) throw new Error(`PACKAGE_EXPORTS: ${specifier} has no ESM export ${key} at ${pkg.directory}/package.json`);
  const filename = resolve(pkg.directory, selected);
  if (relative(pkg.directory, filename).startsWith('..') || !existsSync(filename) || !statSync(filename).isFile()) {
    throw new Error(`PACKAGE_EXPORTS: ${specifier} export ${key} targets missing/invalid ${filename}`);
  }
  return pathToFileURL(filename).href;
}

function peerPackage(pkg: Package, name: string, range: string, anchor: string): Package | undefined {
  const peer = locate(name, anchor);
  if (!peer && pkg.manifest.peerDependenciesMeta?.[name]?.optional) return;
  assertPeerRange(pkg, peer, name, range, anchor);
  return peer;
}
function assertPeerRange(pkg: Package, peer: Package | undefined, name: string, range: string, anchor: string): void {
  if (!peer || !semver.validRange(range) || !semver.satisfies(peer.manifest.version, range)) {
    throw new Error(`PEER_CONFLICT: ${pkg.manifest.name} at ${pkg.directory} requires ${name}@${range}; resolved ${peer?.manifest.version ?? 'missing'} at ${peer?.directory ?? anchor}`);
  }
}
function assertHostPeer(pkg: Package): void {
  if (!pkg.manifest.peerDependencies?.['@society0/host'] && !pkg.manifest.peerDependencies?.['@society0/cordis']) {
    throw new Error(`HOST_PEER_REQUIRED: ${pkg.manifest.name} at ${pkg.directory} must declare @society0/host or @society0/cordis peer`);
  }
}
class PackageGraph {
  private readonly canonical = new Map(['@society0/host', '@society0/cordis'].map(name => [name, required(name, import.meta.url)]));
  private readonly visited = new Set<string>();
  private assertIdentity(pkg: Package, anchor: string): void {
    const {manifest, directory} = pkg;
    const expected = this.canonical.get(manifest.name);
    if (expected && expected.directory !== directory) {
      throw new Error(`FRAMEWORK_DUPLICATE: ${manifest.name}: host ${expected.directory}; plugin ${directory}`);
    }
    if (['@deepseek-ai/cordis', 'cordis'].includes(manifest.name)) {
      throw new Error(`FRAMEWORK_DUPLICATE: host ${this.canonical.get('@society0/cordis')!.directory}; foreign framework ${directory}`);
    }
    for (const [name, canonical] of this.canonical) {
      const resolved = locate(name, anchor);
      if (resolved && resolved.directory !== canonical.directory) {
        throw new Error(`FRAMEWORK_DUPLICATE: ${name} resolved from ${directory}; host ${canonical.directory}; plugin ${resolved.directory}`);
      }
    }
  }
  private inspectPeers(pkg: Package, anchor: string): void {
    for (const [name, range] of Object.entries(pkg.manifest.peerDependencies ?? {})) {
      const peer = peerPackage(pkg, name, range, anchor);
      if (peer) this.inspect(peer);
    }
  }
  inspect(pkg: Package): void {
    if (this.visited.has(pkg.directory)) return;
    this.visited.add(pkg.directory);
    const anchor = pathToFileURL(join(pkg.directory, 'package.json')).href;
    this.assertIdentity(pkg, anchor);
    this.inspectPeers(pkg, anchor);
    for (const name of Object.keys(pkg.manifest.dependencies ?? {})) this.inspect(required(name, anchor));
  }
}
function verifyBatch(graph: PackageGraph, specifiers: readonly string[], baseURL: string): string[] {
  const packages = specifiers.map(specifier => required(packageName(specifier), baseURL));
  // Resolve every export before any import. Export-map semantics belong to resolve.exports.
  const urls = packages.map((pkg, i) => entry(pkg, specifiers[i]!));
  for (const pkg of packages) {
    assertHostPeer(pkg);
    graph.inspect(pkg);
  }
  return urls;
}

/** 一次编译批次的验证图；批次内安装树保持不变，下一批次新建session。 */
export interface PreflightSession {
  preflight(specifiers: readonly string[], baseURL: string): string[];
}
export function createPreflightSession(): PreflightSession {
  let graph: PackageGraph | undefined;
  return {
    preflight(specifiers, baseURL) {
      try {
        graph ??= new PackageGraph();
        return verifyBatch(graph, specifiers, baseURL);
      } catch (error) {
        graph = undefined;
        throw error;
      }
    },
  };
}
/** Validate the complete declared dependency graph before importing boot modules. */
export function preflight(specifiers: readonly string[], baseURL: string): string[] {
  return createPreflightSession().preflight(specifiers, baseURL);
}
