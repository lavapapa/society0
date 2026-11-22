import { mkdir, readFile, readdir, rm, stat, writeFile, copyFile } from "node:fs/promises";
import { dirname, join, resolve, sep } from "node:path";
import { randomUUID } from "node:crypto";
import { atomicJson, fault, jsonFile, syncDirectory } from "../artifacts-local/files.js";
import { nativeJob, transformFile } from "./io.js";
import { PointFiles, type Chain, type PointRecord } from "./points.js";
function inside(parent: string, child: string) {
  const base = resolve(parent),
    target = resolve(child);
  return target === base || target.startsWith(base + sep);
}
/** 只补齐镜像里缺的或长度不同的文件；pack 与 segment 不可变，session 只追加。 */
async function copyMissing(source: string, target: string) {
  const size = (await stat(source)).size;
  const current = await stat(target).then(
    (info) => info.size,
    () => -1,
  );
  if (current === size) return;
  await mkdir(dirname(target), { recursive: true });
  await copyFile(source, target);
}
/** 镜像本运行目录自己的文件（../ 引用的父世界 pack 由父目录自己的镜像负责）。 */
export async function mirrorChain(
  chain: Chain,
  source: string,
  target: string,
  onPhase?: (phase: string, pointId: string) => Promise<void> | void,
) {
  if (inside(source, target) || inside(target, source)) throw fault("HISTORY_MIRROR_PATH");
  const files = new Set<string>([chain.root.path, ...chain.segments.map((segment) => segment.path)]);
  for (const pack of chain.point.packs) if (!pack.path.startsWith("..")) files.add(pack.path);
  for (const id of chain.pointIds) {
    const material = "history/materializations/" + id + ".json";
    if (await stat(join(source, material)).then(() => true, () => false)) files.add(material);
  }
  for (const name of await readdir(join(source, "sessions")).catch(() => [] as string[])) files.add("sessions/" + name);
  for (const name of ["bootstrap.json"]) if (await stat(join(source, name)).then(() => true, () => false)) files.add(name);
  for (const relative of files) {
    await onPhase?.("mirror-file", chain.point.id);
    await copyMissing(join(source, relative), join(target, relative));
  }
  await onPhase?.("mirror-before-descriptor", chain.point.id);
  for (const id of [...chain.pointIds].reverse()) {
    const path = "history/points/" + id + ".json";
    await atomicJson(join(target, path), await jsonFile<PointRecord>(join(source, path), 16 * 1048576), 16 * 1048576);
  }
  await syncDirectory(target);
  await onPhase?.("mirror-descriptor-written", chain.point.id);
}
/** 从镜像恢复：复制整个镜像目录，按最新点重建 world.sqlite。 */
export async function restoreFromMirror(source: string, target: string) {
  if (inside(source, target) || inside(target, source)) throw fault("HISTORY_MIRROR_PATH");
  const walk = async (relative: string): Promise<void> => {
    for (const entry of await readdir(join(source, relative), { withFileTypes: true }).catch(() => []))
      if (entry.isDirectory()) await walk(join(relative, entry.name));
      else await copyMissing(join(source, relative, entry.name), join(target, relative, entry.name));
  };
  await walk("");
  const files = await PointFiles.open(target);
  const latest = files.latest();
  if (!latest) throw fault("HISTORY_NO_POINT");
  const chain = await files.dependencies(latest.id),
    database = join(target, "world.sqlite"),
    staging = join(target, "history", "staging");
  await nativeJob({ operation: "backup", path: files.file(chain.root), target: database });
  await mkdir(staging, { recursive: true });
  for (const segment of chain.segments) {
    const raw = join(staging, randomUUID() + ".changeset");
    let paths: string[] = [raw];
    try {
      await transformFile(files.file(segment), raw, "decompress", 65536, Number(segment.rawBytes) + 1048576);
      if (segment.format === "society0/changeset-container/1") {
        const container = await readFile(raw);
        paths = [];
        for (let offset = 0; offset < container.length; ) {
          const length = Number(container.readBigUInt64BE(offset)),
            path = join(staging, randomUUID() + ".changeset");
          offset += 8;
          await writeFile(path, container.subarray(offset, offset + length), { flag: "wx" });
          paths.push(path);
          offset += length;
        }
      }
      await nativeJob({ operation: "apply", path: database, segments: paths, maxBytes: Math.max(Number(segment.rawBytes), 1) });
    } finally {
      await rm(raw, { force: true });
      for (const path of paths) await rm(path, { force: true });
    }
  }
  return latest;
}
