import { readdir } from "node:fs/promises";
import { join, resolve } from "node:path";
import type { CompletePoint } from "../sqlite-bootstrap/index.js";
import type { PackRef } from "../artifacts-local/store.js";
import { exists, fault, jsonFile } from "../artifacts-local/files.js";
export interface FileRef {
  path: string;
  bytes: string;
}
export interface Segment extends FileRef {
  fromRevision: string;
  toRevision: string;
  rawBytes: string;
  format?: string;
  changesets?: number;
}
/** 点记录。所有路径相对本运行目录；packs 是全量列表（含父世界的 ../ 引用）。 */
export interface PointRecord extends CompletePoint {
  version: 2;
  operationId: string;
  planId: string;
  parentId?: string;
  root?: FileRef;
  segments: Segment[];
  packs: PackRef[];
  /** session 文件名 → 发布时的字节长度（仅本运行目录自己的文件）。 */
  sessions: Record<string, number>;
  requiredOperations: readonly string[];
  /** 分叉来源：父运行目录（相对路径）与父点。 */
  lineage?: { run: string; pointId: string };
}
interface Materialization {
  pointId: string;
  root: FileRef;
}
export interface Chain {
  root: FileRef;
  baseRevision: string;
  segments: Segment[];
  point: PointRecord;
  pointIds: string[];
  points: PointRecord[];
}
/** 点记录全部常驻内存：打开时读一次目录，发布时追加。 */
export class PointFiles {
  private points = new Map<string, PointRecord>();
  private byOperation = new Map<string, string>();
  private latestPoint?: PointRecord;
  constructor(readonly directory: string) {}
  static async open(directory: string) {
    const files = new PointFiles(directory);
    const folder = join(directory, "history", "points");
    for (const name of await readdir(folder))
      if (name.endsWith(".json")) files.published(await jsonFile<PointRecord>(join(folder, name), 16 * 1048576));
    return files;
  }
  file(ref: FileRef) {
    return resolve(this.directory, ref.path);
  }
  pointPath(pointId: string) {
    return join(this.directory, "history", "points", pointId + ".json");
  }
  published(point: PointRecord) {
    this.points.set(point.id, point);
    this.byOperation.set(point.operationId, point.id);
    if (!this.latestPoint || BigInt(point.sequence) > BigInt(this.latestPoint.sequence)) this.latestPoint = point;
  }
  record(pointId: string) {
    const point = this.points.get(pointId);
    if (!point) throw fault("HISTORY_NO_POINT", pointId);
    return point;
  }
  latest() {
    return this.latestPoint;
  }
  findOperation(operationId: string) {
    const pointId = this.byOperation.get(operationId);
    return pointId ? this.points.get(pointId) : undefined;
  }
  *records() {
    yield* this.points.values();
  }
  diagnostics() {
    return { points: this.points.size };
  }
  private async base(point: PointRecord): Promise<Materialization | undefined> {
    const path = join(this.directory, "history", "materializations", point.id + ".json");
    if (await exists(path)) return jsonFile<Materialization>(path);
    if (point.root) return { pointId: point.id, root: point.root };
  }
  /** 从最近的 materialization（或根）开始，收集之后各点的 segment。 */
  async dependencies(pointId: string): Promise<Chain> {
    const after: PointRecord[] = [],
      pointIds: string[] = [];
    let point = this.record(pointId);
    while (true) {
      pointIds.push(point.id);
      const base = await this.base(point);
      if (base) {
        after.reverse();
        return {
          root: base.root,
          baseRevision: point.revision,
          segments: after.flatMap((entry) => entry.segments),
          point: this.record(pointId),
          pointIds,
          points: after,
        };
      }
      after.push(point);
      point = this.record(point.parentId!);
    }
  }
}
