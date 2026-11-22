import { createHash, randomUUID } from "node:crypto";
import { mkdir, open, readdir, rename, rm, stat } from "node:fs/promises";
import { join, resolve } from "node:path";
import type { SqliteTransaction, SqliteMigration } from "../sqlite/contracts.js";
import { Config } from "./config.js";
import { bounded, fault, fileChunks, syncDirectory } from "./files.js";
export interface ArtifactRef {
  readonly id: string;
  readonly bytes: string;
  readonly mediaType: string;
}
/** pack 文件引用；path 相对运行目录（可带 ../ 指向父世界）。 */
export interface PackRef {
  path: string;
  bytes: string;
}
export interface ArtifactOptions {
  directory: string;
  chunkBytes?: number;
  maxObjectBytes?: number;
}
interface Located {
  ref: ArtifactRef;
  file: string;
  offset: number;
}
export const artifactsMigration: SqliteMigration = {
  owner: "artifacts-local",
  from: 0,
  to: 1,
  after: [],
  source: { package: "@society0/core", entry: "artifacts-local", version: 1 },
  statements: [
    {
      sql: "CREATE TABLE s0_artifacts(id TEXT PRIMARY KEY NOT NULL,bytes TEXT NOT NULL,media_type TEXT NOT NULL,revision INTEGER NOT NULL);CREATE INDEX s0_artifacts_revision ON s0_artifacts(revision,id)",
    },
  ],
};
/**
 * 内容寻址对象：stage 写到 objects/staging/<sha>（不 fsync）；发布点时 seal 把本点登记的对象
 * 封成一个 pack（[正文][索引 JSON][8 字节索引长度]），只 fsync 两次。打开时清空暂存区，
 * 由 history 用最新点的 pack 列表调用 load()。
 */
export class LocalArtifacts {
  readonly directory: string;
  readonly chunkBytes: number;
  readonly maxObjectBytes: number;
  private packed = new Map<string, Located>();
  private staged = new Map<string, ArtifactRef>();
  private constructor(options: ArtifactOptions) {
    const config = Config(options);
    this.directory = resolve(config.directory);
    this.chunkBytes = config.chunkBytes;
    this.maxObjectBytes = config.maxObjectBytes;
  }
  static async open(options: ArtifactOptions) {
    const store = new LocalArtifacts(options);
    await rm(join(store.directory, "staging"), { recursive: true, force: true });
    for (const name of ["staging", "packs", "cache"]) await mkdir(join(store.directory, name), { recursive: true });
    return store;
  }
  private stagingPath(id: string) {
    return join(this.directory, "staging", id);
  }
  /** 读入 pack 尾部索引。base 是 pack 路径的基准目录（运行目录）。 */
  async load(packs: readonly PackRef[], base: string) {
    for (const pack of packs) {
      const file = resolve(base, pack.path),
        handle = await open(file, "r");
      try {
        const size = Number(pack.bytes),
          tail = Buffer.alloc(8);
        await handle.read(tail, 0, 8, size - 8);
        const length = Number(tail.readBigUInt64BE()),
          index = Buffer.alloc(length);
        await handle.read(index, 0, length, size - 8 - length);
        for (const entry of JSON.parse(index.toString("utf8")) as {
          id: string;
          offset: number;
          bytes: string;
          mediaType: string;
        }[])
          if (!this.packed.has(entry.id))
            this.packed.set(entry.id, {
              ref: Object.freeze({ id: entry.id, bytes: entry.bytes, mediaType: entry.mediaType }),
              file,
              offset: entry.offset,
            });
      } finally {
        await handle.close();
      }
    }
  }
  async inspect(artifactId: string): Promise<ArtifactRef> {
    const ref = this.packed.get(artifactId)?.ref ?? this.staged.get(artifactId);
    if (!ref) throw fault("ARTIFACT_MISSING", artifactId);
    return ref;
  }
  async stage(
    source: AsyncIterable<Uint8Array>,
    mediaType: string,
    options: { signal?: AbortSignal } = {},
  ): Promise<ArtifactRef> {
    if (typeof mediaType !== "string" || Buffer.byteLength(mediaType) > 1024) throw fault("ARTIFACT_MEDIA_TYPE");
    const temp = join(this.directory, "staging", randomUUID() + ".pending"),
      hash = createHash("sha256"),
      file = await open(temp, "wx");
    let bytes = 0;
    try {
      for await (const chunk of source) {
        options.signal?.throwIfAborted();
        if (!(chunk instanceof Uint8Array) || chunk.byteLength > this.chunkBytes) throw fault("ARTIFACT_CHUNK_BUDGET");
        bytes += chunk.byteLength;
        if (bytes > this.maxObjectBytes) throw fault("ARTIFACT_SIZE_BUDGET");
        hash.update(chunk);
        let offset = 0;
        while (offset < chunk.length)
          offset += (await file.write(chunk, offset, chunk.length - offset, null)).bytesWritten;
      }
    } catch (error) {
      await file.close();
      await rm(temp, { force: true });
      throw error;
    }
    await file.close();
    const id = hash.digest("hex"),
      existing = this.packed.get(id)?.ref ?? this.staged.get(id);
    if (existing) {
      await rm(temp, { force: true });
      return existing;
    }
    await rename(temp, this.stagingPath(id));
    const ref = Object.freeze({ id, bytes: String(bytes), mediaType });
    this.staged.set(id, ref);
    return ref;
  }
  async reference(tx: SqliteTransaction, ref: ArtifactRef) {
    await tx.execute({
      sql: "INSERT INTO s0_artifacts(id,bytes,media_type,revision) VALUES(?,?,?,?) ON CONFLICT(id) DO NOTHING",
      parameters: [ref.id, ref.bytes, ref.mediaType, BigInt(tx.baseRevision) + 1n],
    });
  }
  /** 发布点时调用：把 refs 中仍在暂存区的对象封成一个 pack（返回绝对路径），清空暂存区。没有新对象时返回 undefined。 */
  async seal(refs: readonly ArtifactRef[]): Promise<PackRef | undefined> {
    const fresh = refs.filter((ref) => !this.packed.has(ref.id));
    let pack: PackRef | undefined;
    if (fresh.length) {
      const name = randomUUID() + ".pack",
        target = join(this.directory, "packs", name),
        temp = target + ".pending",
        file = await open(temp, "wx"),
        index: { id: string; offset: number; bytes: string; mediaType: string }[] = [];
      let offset = 0;
      try {
        for (const ref of fresh) {
          if (!this.staged.has(ref.id)) throw fault("ARTIFACT_MISSING", ref.id);
          index.push({ id: ref.id, offset, bytes: ref.bytes, mediaType: ref.mediaType });
          for await (const chunk of fileChunks(this.stagingPath(ref.id), this.chunkBytes)) {
            let written = 0;
            while (written < chunk.length)
              written += (await file.write(chunk, written, chunk.length - written, offset + written)).bytesWritten;
            offset += chunk.length;
          }
        }
        const json = Buffer.from(JSON.stringify(index)),
          tail = Buffer.alloc(8);
        tail.writeBigUInt64BE(BigInt(json.length));
        await file.write(json, 0, json.length, offset);
        await file.write(tail, 0, 8, offset + json.length);
        await file.sync();
        offset += json.length + 8;
      } finally {
        await file.close();
      }
      await rename(temp, target);
      await syncDirectory(join(this.directory, "packs"));
      for (const entry of index)
        this.packed.set(entry.id, { ref: this.staged.get(entry.id)!, file: target, offset: entry.offset });
      pack = { path: target, bytes: String(offset) };
    }
    await this.clearStaging();
    return pack;
  }
  /** 丢弃全部暂存对象（打开时、封存后、回退到点时）。 */
  async clearStaging() {
    this.staged.clear();
    for (const name of await readdir(join(this.directory, "staging")))
      await rm(join(this.directory, "staging", name), { force: true });
  }
  private locate(ref: ArtifactRef) {
    const packed = this.packed.get(ref.id);
    if (packed) return { file: packed.file, offset: packed.offset };
    if (this.staged.has(ref.id)) return { file: this.stagingPath(ref.id), offset: 0 };
    throw fault("ARTIFACT_MISSING", ref.id);
  }
  async *read(
    ref: ArtifactRef,
    range?: { offset: string; length: number },
    options: { signal?: AbortSignal } = {},
  ): AsyncIterable<Uint8Array> {
    const location = this.locate(ref),
      offset = Number(range?.offset ?? 0),
      length = range?.length ?? Number(ref.bytes) - offset;
    bounded(offset, "offset");
    bounded(length, "length");
    if (offset + length > Number(ref.bytes)) throw fault("ARTIFACT_RANGE");
    yield* fileChunks(location.file, this.chunkBytes, location.offset + offset, length, options.signal);
  }
  /** 暂存对象直接给暂存路径；已封存对象写出到 cache/<sha>。 */
  async localFile(ref: ArtifactRef) {
    const location = this.locate(ref);
    if (!this.packed.has(ref.id)) return { path: location.file, release: async () => {} };
    const path = join(this.directory, "cache", ref.id);
    try {
      await stat(path);
    } catch {
      const temp = path + "." + randomUUID() + ".pending",
        file = await open(temp, "wx");
      try {
        for await (const chunk of this.read(ref)) await file.write(chunk);
      } finally {
        await file.close();
      }
      await rename(temp, path);
    }
    return { path, release: async () => {} };
  }
  diagnostics() {
    return { packed: this.packed.size, staged: this.staged.size };
  }
  async close() {}
}
