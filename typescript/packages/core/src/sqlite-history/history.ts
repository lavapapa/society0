import type { SqliteMigration } from "../sqlite/contracts.js";
import { PointFiles, type Chain, type FileRef, type Segment, type PointRecord } from "./points.js";
export type { PointRecord } from "./points.js";
import { Config } from "./config.js";
import { appendFile, mkdir, readdir, rm, stat, readFile, writeFile } from "node:fs/promises";
import { join, relative, resolve, sep } from "node:path";
import { randomUUID } from "node:crypto";
import { isDeepStrictEqual } from "node:util";
import type { Database } from "../sqlite/database.js";
import type { ExecutionGate, PublicationLease } from "../execution-fence/state.js";
import type { CompletePoint, SqliteCreateRequest, RootCreationProbe } from "../sqlite-bootstrap/index.js";
import type { SeedManifest } from "../initializers/index.js";
import type { LocalArtifacts, ArtifactRef, PackRef } from "../artifacts-local/store.js";
import { atomicJson, fault, syncStats } from "../artifacts-local/files.js";
import { nativeJob, transformFile, finalizeFile } from "./io.js";
export interface HistoryOptions {
  directory: string;
  runId: string;
  generation: string;
  controlSequence: string;
  chunkBytes?: number;
  maxSegmentBytes?: number;
  maxRootBytes?: number;
  maxManifestBytes?: number;
  applyBatchSegments?: number;
  applyBatchBytes?: number;
  compactEvery?: number;
  mirrorDirectory?: string;
  mirrorPolicy?: "every-point" | "interval";
  mirrorIntervalMs?: number;
  mirrorMaxLagPoints?: number;
  onPhase?: (phase: string, operationId: string) => Promise<void> | void;
}
export interface CompletePointRequest {
  operationId: string;
  id: string;
  parentId: string;
  expectedRevision: string;
  requiredOperations: readonly string[];
}
export interface ForkRequest {
  operationId: string;
  pointId: string;
  targetRunId: string;
  targetDirectory: string;
  initialControl?: SqliteCreateRequest["initialControl"];
  migrations?: readonly SqliteMigration[];
}
export type ForkProbe =
  | { status: "prepared"; point: PointRecord }
  | { status: "not-prepared" }
  | { status: "unknown"; reason: string };
/** session 日志与点的交汇：发布时取各文件长度（先 fsync），恢复时截断到点上的长度。 */
export interface SessionPort {
  heads(): Promise<Record<string, number>>;
  truncate(heads: Record<string, number>): Promise<void>;
}
export interface HistoryDependencies {
  database: Pick<Database, "runId" | "readRevision" | "snapshot" | "metadata" | "activateFromCompletePoint" | "diagnostics">;
  fence: Pick<ExecutionGate, "seal" | "assertPublication" | "resume">;
  artifacts: Pick<LocalArtifacts, "inspect" | "seal" | "load" | "clearStaging">;
}
type HistorySnapshot = Awaited<ReturnType<HistoryDependencies["database"]["snapshot"]>>;
type PublishReport = {
  pointId: string;
  publishMs: number;
  fsyncs: number;
  commits: number;
  segments: number;
  segmentBytes: number;
  packs: number;
  packBytes: number;
};
/**
 * 完整点：world.sqlite 的增量 segment + 本点新对象的 pack + session 长度。
 * 前提（设计稿 §9）：单进程、崩溃只从完整点继续、分叉只在点上，因此不做幂等探测与自写文件校验。
 */
export class History {
  readonly directory: string;
  private options: Required<Omit<HistoryOptions, "onPhase" | "mirrorDirectory">> &
    Pick<HistoryOptions, "onPhase" | "mirrorDirectory">;
  private closing = false;
  private active = new Set<Promise<unknown>>();
  private publishing = false;
  private rootLease?: PublicationLease;
  private sessions?: SessionPort;
  private quiescence?: () => Promise<void>;
  private abort = new AbortController();
  private closed?: Promise<void>;
  private timings: Record<string, { count: number; failed: number; totalMs: number }> = {};
  private metrics = {
    backupBytes: 0,
    applyCount: 0,
    rootCopies: 0,
    segmentsSealed: 0,
    packsSealed: 0,
    compressedInputBytes: 0,
    compressedOutputBytes: 0,
    maxRSS: 0,
    lastNative: undefined as unknown,
    rawCollected: 0,
    rawBytesCollected: 0,
    lastGcError: undefined as string | undefined,
    lastCompactionError: undefined as string | undefined,
    lastMirrorError: undefined as string | undefined,
    publishMs: 0,
    publishCount: 0,
    lastMetricsError: undefined as string | undefined,
  };
  private syncBase = syncStats.calls;
  private mirror?: { directory: string; maxLagPoints: number; onPhase?: (phase: string, pointId: string) => Promise<void> | void };
  private mirrorQueue: string[] = [];
  private mirroring = false;
  private mirrorWaiters = new Set<() => void>();
  private onApplyBatch?: (batch: { index: number; segments: readonly Segment[]; toRevision: string }) => Promise<void> | void;
  private onMetrics?: (report: PublishReport) => void;
  private constructor(
    options: HistoryOptions,
    private deps: HistoryDependencies,
    private files: PointFiles,
  ) {
    this.directory = resolve(options.directory);
    this.options = { ...Config(options), directory: this.directory, onPhase: options.onPhase };
    if (deps.database.runId !== options.runId) throw fault("HISTORY_RUN_ID");
  }
  static async open(options: HistoryOptions, deps: HistoryDependencies) {
    const directory = resolve(options.directory);
    for (const name of ["points", "roots", "segments", "materializations", "staging"])
      await mkdir(join(directory, "history", name), { recursive: true });
    const history = new History(options, deps, await PointFiles.open(directory));
    const latest = history.files.latest();
    if (latest) await deps.artifacts.load(latest.packs, directory);
    if (history.options.mirrorDirectory)
      history.bindMirror({
        directory: history.options.mirrorDirectory,
        maxLagPoints: history.options.mirrorMaxLagPoints,
        onPhase: options.onPhase,
      });
    return history;
  }
  private available() {
    if (this.closing) throw fault("HISTORY_CLOSED");
  }
  private recordTiming(phase: string, ms: number, failed = false) {
    const timing = (this.timings[phase] ??= { count: 0, failed: 0, totalMs: 0 });
    timing.count++;
    timing.totalMs += ms;
    if (failed) timing.failed++;
  }
  private async measure<T>(phase: string, body: () => Promise<T>): Promise<T> {
    const started = performance.now();
    let failed = false;
    try {
      return await body();
    } catch (error) {
      failed = true;
      throw error;
    } finally {
      this.recordTiming(phase, performance.now() - started, failed);
    }
  }
  private work<T>(body: () => Promise<T>, phase: string): Promise<T> {
    this.available();
    const task = this.measure(phase, body);
    this.active.add(task);
    void task.then(
      () => this.active.delete(task),
      () => this.active.delete(task),
    );
    return task;
  }
  private async job(request: Record<string, unknown>) {
    const result = await this.measure("native." + request.operation, () =>
      nativeJob(
        { ...request, chunkBytes: this.options.chunkBytes, maxBytes: request.maxBytes ?? this.options.maxRootBytes },
        this.abort.signal,
      ),
    );
    this.metrics.maxRSS = Math.max(this.metrics.maxRSS, result.metrics.maxRSS);
    this.metrics.lastNative = result.metrics.native;
    if (request.operation === "backup" || request.operation === "backupVerified")
      this.metrics.backupBytes += Number(result.result?.bytes ?? 0);
    if (request.operation === "apply") this.metrics.applyCount += (request.segments as string[]).length;
    return result.result;
  }
  private pointPath(directory: string, pointId: string) {
    return join(directory, "history", "points", pointId + ".json");
  }
  private latest() {
    return this.files.latest();
  }
  async current(): Promise<CompletePoint> {
    this.available();
    if (this.publishing) throw fault("HISTORY_BUSY");
    const point = this.latest();
    if (!point) throw fault("HISTORY_NO_POINT");
    return structuredClone(point);
  }
  async describe(pointId: string): Promise<PointRecord> {
    this.available();
    return structuredClone(this.files.record(pointId));
  }
  bindIOObserver(callback: (phase: string, operationId: string) => Promise<void> | void) {
    if (this.options.onPhase) throw fault("HISTORY_IO_OBSERVER_BOUND");
    this.options.onPhase = callback;
    return () => {
      if (this.options.onPhase === callback) this.options.onPhase = undefined;
    };
  }
  bindApplyBatch(callback: NonNullable<History["onApplyBatch"]>) {
    if (this.onApplyBatch) throw fault("HISTORY_APPLY_OBSERVER_BOUND");
    this.onApplyBatch = callback;
    return () => {
      if (this.onApplyBatch === callback) this.onApplyBatch = undefined;
    };
  }
  bindMetrics(callback: (report: PublishReport) => void) {
    if (this.onMetrics) throw fault("HISTORY_METRICS_BOUND");
    this.onMetrics = callback;
    return () => {
      if (this.onMetrics === callback) this.onMetrics = undefined;
    };
  }
  bindQuiescence(callback: () => Promise<void>) {
    if (this.quiescence) throw fault("HISTORY_QUIESCENCE_BOUND");
    this.quiescence = callback;
    return () => {
      if (this.quiescence === callback) this.quiescence = undefined;
    };
  }
  bindSessions(port: SessionPort) {
    if (this.sessions) throw fault("HISTORY_SESSIONS_BOUND");
    this.sessions = port;
    return () => {
      if (this.sessions === port) this.sessions = undefined;
    };
  }
  bindMirror(options: {
    directory: string;
    maxLagPoints?: number;
    onPhase?: (phase: string, pointId: string) => Promise<void> | void;
  }) {
    if (this.mirror) throw fault("HISTORY_MIRROR_BOUND");
    const directory = resolve(options.directory);
    if (directory === this.directory || directory.startsWith(this.directory + sep) || this.directory.startsWith(directory + sep))
      throw fault("HISTORY_MIRROR_PATH");
    this.mirror = { directory, maxLagPoints: options.maxLagPoints ?? 3, onPhase: options.onPhase };
    return () => {
      if (this.mirror?.directory === directory) this.mirror = undefined;
    };
  }
  private enqueueMirror(pointId: string) {
    if (!this.mirror) return;
    this.mirrorQueue.push(pointId);
    void this.drainMirror();
  }
  private async drainMirror() {
    if (this.mirroring || !this.mirror) return;
    this.mirroring = true;
    try {
      while (this.mirrorQueue.length) {
        const pointId = this.mirrorQueue.shift()!;
        try {
          const { mirrorChain } = await import("./mirror.js");
          await mirrorChain(await this.files.dependencies(pointId), this.directory, this.mirror.directory, this.mirror.onPhase);
        } catch (error) {
          this.metrics.lastMirrorError = String(error);
        }
      }
    } finally {
      this.mirroring = false;
      for (const resolve of this.mirrorWaiters) resolve();
      this.mirrorWaiters.clear();
    }
  }
  async flushMirror() {
    while (this.mirrorQueue.length || this.mirroring) await new Promise<void>((resolve) => this.mirrorWaiters.add(resolve));
  }
  /** 删掉已被点上的 segment 覆盖的原始 changeset。 */
  private async collectRaw(upTo: string) {
    try {
      const dir = join(this.directory, "segments");
      for (const name of await readdir(dir).catch(() => [] as string[])) {
        const match = /^(\d+)\.changeset$/.exec(name);
        if (!match || BigInt(match[1]!) > BigInt(upTo)) continue;
        const path = join(dir, name);
        this.metrics.rawBytesCollected += (await stat(path)).size;
        await rm(path);
        this.metrics.rawCollected++;
      }
    } catch (error) {
      this.metrics.lastGcError = String(error);
    }
  }
  /** sqliteBootstrap 的根点探测：点记录在内存里，按 operationId 直接查。 */
  async probeRoot(directory: string, operationId: string): Promise<RootCreationProbe> {
    if (resolve(directory) !== this.directory) return { status: "unknown", reason: "run directory differs" };
    const point = this.files.findOperation(operationId);
    return point ? { status: "created", root: structuredClone(point) } : { status: "not-started" };
  }
  private async artifactRefs(snapshot: HistorySnapshot, since?: string) {
    const refs: ArtifactRef[] = [],
      until = BigInt(snapshot.revision);
    let revision = BigInt(since ?? "-1"),
      id = "￿";
    while (true) {
      const rows = await snapshot.query(
        {
          sql: "SELECT id,bytes,media_type,revision FROM s0_artifacts WHERE (revision>? OR (revision=? AND id>?)) AND revision<=? ORDER BY revision,id LIMIT 2000",
          parameters: [revision, revision, id, until],
        },
        { maxRows: 2000, maxBytes: 1048576 },
      );
      for (const row of rows) refs.push({ id: String(row.id), bytes: String(row.bytes), mediaType: String(row.media_type) });
      if (rows.length < 2000) return refs;
      revision = BigInt(rows.at(-1)!.revision as bigint);
      id = String(rows.at(-1)!.id);
    }
  }
  private async facts(since?: string) {
    const snapshot = await this.deps.database.snapshot({ maxAgeMs: 30000 });
    try {
      const frontier = await this.deps.database.metadata.read(snapshot);
      const artifacts = await this.artifactRefs(snapshot, since);
      return { revision: snapshot.revision, schema: snapshot.schema, frontier, artifacts };
    } finally {
      await snapshot.close();
    }
  }
  /** 封存本点新对象，返回全量 pack 列表（路径相对运行目录）。 */
  private async sealPacks(inherited: readonly PackRef[], refs: readonly ArtifactRef[]) {
    const pack = await this.measure("packSeal", () => this.deps.artifacts.seal(refs));
    if (!pack) return [...inherited];
    this.metrics.packsSealed++;
    return [...inherited, { path: relative(this.directory, pack.path), bytes: pack.bytes }];
  }
  private async compress(source: string, rawBytes: number, fromRevision: string, toRevision: string, extra: Partial<Segment> = {}): Promise<Segment> {
    const name = randomUUID() + ".zst",
      target = join(this.directory, "history", "segments", name),
      temp = target + ".pending";
    const stream = await this.measure("compressPipeline", () =>
      transformFile(source, temp, "compress", this.options.chunkBytes, this.options.maxSegmentBytes + 1048576, this.abort.signal),
    );
    await this.measure("fileFinalizeIO", () => finalizeFile(temp, target));
    this.metrics.segmentsSealed++;
    this.metrics.compressedInputBytes += stream.inputBytes;
    this.metrics.compressedOutputBytes += stream.bytes;
    return { path: "history/segments/" + name, bytes: String(stream.bytes), rawBytes: String(rawBytes), fromRevision, toRevision, ...extra };
  }
  private async sealSegments(parentRevision: string, revision: string): Promise<Segment[]> {
    const numbers: bigint[] = [];
    for (let n = BigInt(parentRevision) + 1n; n <= BigInt(revision); n++) numbers.push(n);
    if (!numbers.length) return [];
    const sources = numbers.map((n) => join(this.directory, "segments", n + ".changeset"));
    if (numbers.length === 1) return [await this.compress(sources[0]!, (await stat(sources[0]!)).size, parentRevision, revision)];
    const container = join(this.directory, "history", "staging", randomUUID() + ".changesets");
    let total = 0;
    for (const source of sources) {
      const bytes = await readFile(source),
        header = Buffer.alloc(8);
      header.writeBigUInt64BE(BigInt(bytes.length));
      await appendFile(container, Buffer.concat([header, bytes]));
      total += bytes.length + 8;
    }
    try {
      return [
        await this.compress(container, total, parentRevision, revision, {
          format: "society0/changeset-container/1",
          changesets: sources.length,
        }),
      ];
    } finally {
      await rm(container, { force: true });
    }
  }
  private async decodeSegment(segment: Segment): Promise<string[]> {
    const staging = join(this.directory, "history", "staging"),
      raw = join(staging, randomUUID() + ".changeset");
    await this.measure("decompressPipeline", () =>
      transformFile(
        this.files.file(segment),
        raw,
        "decompress",
        this.options.chunkBytes,
        Math.max(this.options.maxSegmentBytes, Number(segment.rawBytes) + 1048576),
        this.abort.signal,
      ),
    );
    if (segment.format !== "society0/changeset-container/1") return [raw];
    const container = await readFile(raw);
    await rm(raw);
    const paths: string[] = [];
    for (let offset = 0; offset < container.length; ) {
      const length = Number(container.readBigUInt64BE(offset)),
        path = join(staging, randomUUID() + ".changeset");
      offset += 8;
      await writeFile(path, container.subarray(offset, offset + length), { flag: "wx" });
      paths.push(path);
      offset += length;
    }
    return paths;
  }
  private applyBatches(chain: Chain) {
    const boundaries = new Set<string>([chain.baseRevision, ...chain.points.map((point) => point.revision)]);
    const batches: { segments: Segment[]; toRevision: string }[] = [];
    let current: Segment[] = [],
      bytes = 0;
    chain.segments.forEach((segment, index) => {
      current.push(segment);
      bytes += Number(segment.rawBytes);
      const last = index === chain.segments.length - 1;
      if (
        (boundaries.has(segment.toRevision) || last) &&
        (current.length >= this.options.applyBatchSegments || bytes >= this.options.applyBatchBytes || last)
      ) {
        batches.push({ segments: current, toRevision: segment.toRevision });
        current = [];
        bytes = 0;
      }
    });
    return batches;
  }
  private async rootFile(source: string, directory = this.directory) {
    const name = randomUUID() + ".sqlite",
      target = join(directory, "history", "roots", name),
      temp = target + ".pending";
    await this.job({ operation: "backup", path: source, target: temp });
    await this.measure("fileFinalizeIO", () => finalizeFile(temp, target));
    this.metrics.rootCopies++;
    return { path: "history/roots/" + name, bytes: String((await stat(target)).size) };
  }
  private async commitDescriptor(point: PointRecord) {
    this.abort.signal.throwIfAborted();
    await this.options.onPhase?.("before-descriptor", point.operationId);
    await atomicJson(this.pointPath(this.directory, point.id), point, this.options.maxManifestBytes, (phase) =>
      this.options.onPhase?.(phase, point.operationId),
    );
    this.files.published(point);
    await this.options.onPhase?.("descriptor-published", point.operationId);
  }
  private afterPublish(point: PointRecord, fresh: readonly PackRef[], started: number, syncs: number, commits: number) {
    const publishMs = performance.now() - started;
    this.metrics.publishMs += publishMs;
    this.metrics.publishCount++;
    try {
      this.onMetrics?.({
        pointId: point.id,
        publishMs,
        fsyncs: syncStats.calls - syncs,
        commits: this.deps.database.diagnostics().commits - commits,
        segments: point.segments.length,
        segmentBytes: point.segments.reduce((n, segment) => n + Number(segment.bytes), 0),
        packs: fresh.length,
        packBytes: fresh.reduce((n, pack) => n + Number(pack.bytes), 0),
      });
    } catch (error) {
      this.metrics.lastMetricsError = String(error);
    }
    this.enqueueMirror(point.id);
  }
  publishRoot(request: SqliteCreateRequest, seed: SeedManifest): Promise<CompletePoint> {
    return this.work(async () => {
      const started = performance.now(),
        syncs = syncStats.calls,
        commits = this.deps.database.diagnostics().commits;
      const lease = this.rootLease ?? (await this.deps.fence.seal());
      this.deps.fence.assertPublication(lease);
      this.rootLease = lease;
      this.publishing = true;
      try {
        const facts = await this.facts();
        const objects = new Map(facts.artifacts.map((ref) => [ref.id, ref]));
        for (const artifact of seed.artifactIds) objects.set(artifact, await this.deps.artifacts.inspect(artifact));
        const packs = await this.sealPacks([], [...objects.values()]),
          sessions = (await this.sessions?.heads()) ?? {},
          root = await this.rootFile(join(this.directory, "world.sqlite"));
        const point: PointRecord = {
          version: 2,
          id: randomUUID(),
          operationId: request.operationId,
          runId: request.runId,
          kind: "root",
          sequence: "0",
          revision: facts.revision,
          generation: lease.generation,
          controlSequence: request.initialControl.controlSequence,
          schema: facts.schema,
          frontier: facts.frontier,
          root,
          segments: [],
          packs,
          sessions,
          requiredOperations: seed.operationIds,
          planId: request.initialControl.planId,
        };
        await this.commitDescriptor(point);
        this.afterPublish(point, point.packs, started, syncs, commits);
        await this.collectRaw(point.revision);
        if (this.deps.database.diagnostics().diagnostic) await this.deps.database.activateFromCompletePoint(lease, point);
        this.deps.fence.resume(lease);
        this.rootLease = undefined;
        return structuredClone(point);
      } finally {
        this.publishing = false;
      }
    }, "rootPublication");
  }
  publish(lease: PublicationLease, request: CompletePointRequest): Promise<CompletePoint> {
    return this.work(async () => {
      const started = performance.now(),
        syncs = syncStats.calls,
        commits = this.deps.database.diagnostics().commits;
      await this.quiescence?.();
      this.deps.fence.assertPublication(lease);
      const parent = this.latest()!;
      this.publishing = true;
      try {
        const facts = await this.facts(parent.revision),
          packs = await this.sealPacks(parent.packs, facts.artifacts),
          sessions = (await this.sessions?.heads()) ?? {},
          segments = await this.sealSegments(parent.revision, facts.revision);
        const point: PointRecord = {
          version: 2,
          id: request.id,
          operationId: request.operationId,
          runId: this.options.runId,
          kind: "advance",
          sequence: String(BigInt(parent.sequence) + 1n),
          parentId: parent.id,
          revision: facts.revision,
          generation: lease.generation,
          controlSequence: this.options.controlSequence,
          schema: facts.schema,
          frontier: facts.frontier,
          segments,
          packs,
          sessions,
          requiredOperations: request.requiredOperations,
          planId: parent.planId,
        };
        await this.commitDescriptor(point);
        this.afterPublish(point, point.packs.slice(parent.packs.length), started, syncs, commits);
        await this.options.onPhase?.("raw-collecting", request.operationId);
        await this.collectRaw(point.revision);
        if (this.options.compactEvery && BigInt(point.sequence) % BigInt(this.options.compactEvery) === 0n) {
          try {
            await this.compactPoint(point.id);
          } catch (error) {
            this.metrics.lastCompactionError = String(error);
          }
        }
        return structuredClone(point);
      } finally {
        this.publishing = false;
      }
    }, "publication");
  }
  async status() {
    try {
      const completePoint = await this.current(),
        liveRevision = await this.deps.database.readRevision();
      const snapshot = await this.deps.database.snapshot({ maxAgeMs: 30000 });
      try {
        const frontier = await this.deps.database.metadata.read(snapshot);
        return {
          liveRevision,
          completePoint,
          state:
            liveRevision === completePoint.revision && isDeepStrictEqual(frontier, completePoint.frontier)
              ? ("clean" as const)
              : BigInt(liveRevision) > BigInt(completePoint.revision)
                ? ("dirty" as const)
                : ("unknown" as const),
        };
      } finally {
        await snapshot.close();
      }
    } catch (error) {
      return { state: "unknown" as const, reason: String(error) };
    }
  }
  /** 续跑/分叉打开：live 库必须正好停在最新点；session 截到点上的长度，暂存对象作废。 */
  async activate() {
    const status = await this.status();
    if (status.state !== "clean") throw fault("HISTORY_" + status.state.toUpperCase());
    const point = this.latest()!;
    await this.sessions?.truncate(point.sessions);
    await this.deps.artifacts.clearStaging();
    const lease = this.rootLease ?? (await this.deps.fence.seal());
    this.deps.fence.assertPublication(lease);
    if (this.deps.database.diagnostics().diagnostic) await this.deps.database.activateFromCompletePoint(lease, status.completePoint!);
    this.deps.fence.resume(lease);
    this.rootLease = undefined;
    return status.completePoint!;
  }
  /** 从最近的 materialization 重放到 pointId；目标若是最新点，直接 backup live 库。 */
  private async materialize(pointId: string, target: string) {
    const chain = await this.files.dependencies(pointId);
    if (this.latest()?.id === pointId) {
      const copied = await this.job({ operation: "backupVerified", path: join(this.directory, "world.sqlite"), target, expected: chain.point });
      if (copied.matched) return chain;
    }
    await this.job({ operation: "backup", path: this.files.file(chain.root), target });
    const batches = this.applyBatches(chain);
    for (let index = 0; index < batches.length; index++) {
      const batch = batches[index]!,
        paths: string[] = [];
      try {
        for (const segment of batch.segments) paths.push(...(await this.decodeSegment(segment)));
        await this.onApplyBatch?.({ index, segments: batch.segments, toRevision: batch.toRevision });
        await this.job({
          operation: "apply",
          path: target,
          segments: paths,
          maxBytes: Math.max(...batch.segments.map((segment) => Number(segment.rawBytes))),
        });
      } finally {
        for (const path of paths) await rm(path, { force: true });
      }
    }
    return chain;
  }
  /**
   * 分叉：子目录只放 world.sqlite、根点记录（引用父 pack）、sessions/parent.json。
   * 不复制对象、不复制 session。失败时调用方删掉目标目录重来。
   */
  fork(request: ForkRequest): Promise<CompletePoint> {
    return this.work(async () => {
      const target = resolve(request.targetDirectory),
        phases: Record<string, number> = {},
        timed = async <T>(phase: string, body: () => Promise<T>) => {
          const started = performance.now();
          try {
            return await this.measure("fork." + phase, body);
          } finally {
            phases[phase] = performance.now() - started;
          }
        };
      for (const name of ["points", "roots", "segments", "materializations", "staging"])
        await mkdir(join(target, "history", name), { recursive: true });
      await mkdir(join(target, "sessions"), { recursive: true });
      const chain = await timed("materialize", () => this.materialize(request.pointId, join(target, "world.sqlite")));
      const source = chain.point,
        control = request.initialControl ?? { generation: source.generation, controlSequence: source.controlSequence, planId: source.planId };
      const actual = await timed("prepare", () =>
        this.job({
          operation: "prepareFork",
          path: join(target, "world.sqlite"),
          directory: target,
          runId: request.targetRunId,
          operationId: request.operationId,
          migrations: request.migrations,
        }),
      );
      const root = await timed("root", () => this.rootFile(join(target, "world.sqlite"), target));
      const parentRun = relative(target, this.directory);
      const point: PointRecord = {
        version: 2,
        id: randomUUID(),
        operationId: request.operationId,
        runId: request.targetRunId,
        kind: "root",
        sequence: "0",
        revision: actual.revision,
        generation: control.generation,
        controlSequence: control.controlSequence,
        schema: actual.schema,
        frontier: actual.frontier,
        root,
        segments: [],
        packs: source.packs.map((pack) => ({ path: relative(target, resolve(this.directory, pack.path)), bytes: pack.bytes })),
        sessions: {},
        requiredOperations: [...source.requiredOperations, ...actual.seed.operationIds],
        planId: control.planId,
        lineage: { run: parentRun, pointId: source.id },
      };
      await timed("descriptor", async () => {
        await atomicJson(join(target, "sessions", "parent.json"), { run: parentRun, heads: source.sessions });
        await atomicJson(this.pointPath(target, point.id), point, this.options.maxManifestBytes);
      });
      await appendFile(
        join(target, "metrics.jsonl"),
        JSON.stringify({ type: "fork", time: new Date().toISOString(), sourcePointId: source.id, packs: point.packs.length, phases }) + "\n",
      );
      return structuredClone(point);
    }, "fork");
  }
  /** 目标目录里已有根点记录即视为已准备好。 */
  async probeFork(request: { operationId: string; targetDirectory: string }): Promise<ForkProbe> {
    try {
      const files = await PointFiles.open(resolve(request.targetDirectory));
      const point = files.findOperation(request.operationId);
      return point ? { status: "prepared", point: structuredClone(point) } : { status: "not-prepared" };
    } catch {
      return { status: "not-prepared" };
    }
  }
  compact(pointId: string): Promise<void> {
    return this.work(() => this.compactPoint(pointId), "compaction");
  }
  private async compactPoint(pointId: string) {
    const temporary = join(this.directory, "history", "staging", randomUUID() + ".sqlite");
    try {
      await this.materialize(pointId, temporary);
      const root = await this.rootFile(temporary);
      await this.options.onPhase?.("before-materialization", pointId);
      await atomicJson(join(this.directory, "history", "materializations", pointId + ".json"), { pointId, root }, this.options.maxManifestBytes);
    } finally {
      await rm(temporary, { force: true });
    }
  }
  diagnostics() {
    return {
      ...this.metrics,
      fsyncs: syncStats.calls - this.syncBase,
      ...this.files.diagnostics(),
      timings: structuredClone(this.timings),
      maxRSS: Math.max(this.metrics.maxRSS, process.resourceUsage().maxRSS),
      active: this.active.size,
      publishing: this.publishing,
      closing: this.closing,
    };
  }
  close(): Promise<void> {
    return (this.closed ??= (async () => {
      await this.flushMirror().catch(() => {});
      this.closing = true;
      this.abort.abort(new Error("history closing"));
      await Promise.allSettled([...this.active]);
    })());
  }
}
export type { FileRef };
