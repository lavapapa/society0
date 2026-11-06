import type { Json } from './common.js';
import type { DomainVersion } from './data.js';
import type { SqliteReader } from './eligibility.js';
import type { SqliteReadView } from '../sqlite/contracts.js';
import type { DeliveryChannel } from './delivery.js';

const coded = (code: string) => Object.assign(new Error(code), { code });

/** 目标对象引用。kind 不得含 ':'；文本形 `${kind}:${id}`，id 可以含 ':'。 */
export interface TargetRef {
    readonly kind: string;
    readonly id: string;
}

export const targetText = (target: TargetRef): string => `${target.kind}:${target.id}`;

/** 只在第一个 ':' 处切分；kind 为空或 id 为空时报 ACTION_TARGET_INVALID。 */
export function parseTarget(text: string): TargetRef {
    const index = text.indexOf(':');
    if (index <= 0 || index === text.length - 1)
        throw coded('ACTION_TARGET_INVALID');
    return { kind: text.slice(0, index), id: text.slice(index + 1) };
}

export interface ActionTargets {
    readonly kinds: readonly string[];
    readonly required: boolean;
}

export type ActionExposure = 'offer' | 'tool' | 'internal';

/** 一条可供性。key = action + '@' + targetText(target ?? '-')。 */
export interface ActionOffer {
    readonly action: string;
    readonly target?: TargetRef;
    readonly prefill?: Json;
    readonly locked?: readonly string[];
    readonly needs?: readonly string[];
    readonly reason?: string;
    readonly until?: Json;
    readonly priority?: number;
    readonly provider: string;
}

export interface OfferIdentity {
    readonly key: string;
    readonly provider: string;
}

export interface OfferQuery {
    readonly actorId: string;
    readonly momentId: string;
    readonly logicalTime?: Json;
    readonly action?: string;
    readonly targets?: readonly TargetRef[];
    readonly maxItems: number;
}

export interface OfferProvider {
    readonly id: string;
    readonly actions: readonly string[];
    versions(query: OfferQuery, reader: SqliteReader): Promise<readonly DomainVersion[]>;
    offers(query: OfferQuery, reader: SqliteReader): Promise<readonly Omit<ActionOffer, 'provider'>[]>;
}

/** schema 卡片。targets 是允许的 kind 列表，与 DESIGN §4.4 的 `"targets":["social/post"]` 一致。 */
export interface ActionSchemaCard {
    readonly action: string;
    readonly digest: string;
    readonly description?: string;
    readonly input: Json;
    readonly targets?: readonly string[];
}

/** 数据内嵌的统一引用形状（/public 渲染器、感知材料、briefing 一律使用）。 */
export interface ActionRef {
    readonly act: string;
    readonly target?: string;
    readonly prefill?: Json;
    readonly needs?: readonly string[];
}

export interface OmittedOffer {
    readonly action: string;
    readonly count: number;
}

export interface OfferSet {
    readonly offers: readonly ActionOffer[];
    readonly schemas: ReadonlyMap<string, ActionSchemaCard>;
    readonly omitted: readonly OmittedOffer[];
    readonly versions: readonly DomainVersion[];
}

export interface OfferLimits {
    readonly maxOffers: number;
    readonly maxOfferBytes: number;
    readonly maxSchemaBytes: number;
}

/** 一条待送达的 offer：offer 本体、变更判定摘要、可选的 schema 卡片。 */
export interface OfferEntry {
    readonly offer: ActionOffer;
    readonly digest: string;
    readonly schema?: ActionSchemaCard;
}

/** 历史中的结构化副本，对应 originalProviderFields.offers 或 details.offers。 */
export interface OfferRecord {
    readonly added?: readonly (ActionOffer | OfferEntry)[];
    readonly expired?: readonly string[];
    readonly schemas?: readonly string[];
}

export interface OfferFoldState {
    readonly delivered: ReadonlyMap<string, OfferEntry>;
    readonly schemas: ReadonlySet<string>;
}

export interface OfferDelta {
    readonly initial: boolean;
    readonly added: readonly ActionOffer[];
    readonly entries: readonly OfferEntry[];
    readonly changed: readonly ActionOffer[];
    readonly expired: readonly string[];
    readonly folded: readonly string[];
    readonly schemas: readonly ActionSchemaCard[];
    readonly omitted: readonly OmittedOffer[];
}

export interface OfferLabels {
    readonly full: string;
    readonly change: string;
    readonly prefill: string;
    readonly locked: string;
    readonly needs: string;
    readonly omitted: string;
    readonly listPath: string;
}

export const DEFAULT_OFFER_LABELS: OfferLabels = {
    full: '可用行动',
    change: '可用行动（变更）',
    prefill: '预填',
    locked: '锁定',
    needs: '需填',
    omitted: '另有',
    listPath: '/self/actions/offers.jsonl',
};

function compareText(left: string, right: string): number {
    const a = [...left], b = [...right];
    for (let index = 0; index < Math.min(a.length, b.length); index++) {
        const difference = a[index]!.codePointAt(0)! - b[index]!.codePointAt(0)!;
        if (difference !== 0)
            return difference;
    }
    return a.length - b.length;
}

/** 规范化紧凑 JSON：键按 code point 排序，无空格。用于 schema 之外的 JSON（DESIGN §4.4）。 */
export function canonicalJson(value: Json): string {
    if (value === null || typeof value !== 'object')
        return JSON.stringify(value);
    if (Array.isArray(value))
        return `[${value.map(canonicalJson).join(',')}]`;
    const object = value as Readonly<Record<string, Json>>;
    const keys = Object.keys(object).filter((key) => object[key] !== undefined).sort(compareText);
    return `{${keys.map((key) => `${JSON.stringify(key)}:${canonicalJson(object[key]!)}`).join(',')}}`;
}

export function offerKey(offer: Pick<ActionOffer, 'action' | 'target'>): string {
    return `${offer.action}@${offer.target ? targetText(offer.target) : '-'}`;
}

/** 变更判定摘要：只覆盖影响送达内容的字段；until 只用于展示，不参与。 */
export function offerDigest(offer: ActionOffer): string {
    const normalized: Record<string, Json> = { action: offer.action, provider: offer.provider };
    if (offer.target)
        normalized.target = { kind: offer.target.kind, id: offer.target.id };
    if (offer.prefill !== undefined)
        normalized.prefill = offer.prefill;
    if (offer.locked !== undefined)
        normalized.locked = [...offer.locked];
    if (offer.needs !== undefined)
        normalized.needs = [...offer.needs];
    if (offer.reason !== undefined)
        normalized.reason = offer.reason;
    if (offer.priority !== undefined)
        normalized.priority = offer.priority;
    return canonicalJson(normalized);
}

export function schemaCard(action: string, definition: { readonly description?: string; readonly input: Json; readonly targets?: ActionTargets }): ActionSchemaCard {
    const targets = definition.targets ? [...definition.targets.kinds] : undefined;
    const body = {
        ...(definition.description !== undefined ? { description: definition.description } : {}),
        input: definition.input,
        ...(targets !== undefined ? { targets } : {}),
    };
    return { action, digest: canonicalJson(body), description: definition.description, input: definition.input, targets };
}

export function offerEntry(offer: ActionOffer, schema?: ActionSchemaCard): OfferEntry {
    return { offer, digest: offerDigest(offer), ...(schema ? { schema } : {}) };
}

/** schema 行保持定义中的键顺序；其余 JSON 用 canonicalJson。 */
export function schemaLine(card: ActionSchemaCard): string {
    const body: Record<string, Json> = {};
    if (card.description !== undefined)
        body.description = card.description;
    body.input = card.input;
    if (card.targets !== undefined)
        body.targets = [...card.targets];
    return `schema ${card.action} ${JSON.stringify(body)}`;
}

export function offerLine(entry: OfferEntry, labels: OfferLabels = DEFAULT_OFFER_LABELS): string {
    const offer = entry.offer;
    let line = `+ ${offer.action}`;
    if (offer.target)
        line += ` ${targetText(offer.target)}`;
    if (offer.prefill !== undefined)
        line += `  ${labels.prefill}${canonicalJson(offer.prefill)}`;
    if (offer.locked?.length)
        line += ` ${labels.locked}[${offer.locked.join(',')}]`;
    if (offer.needs?.length)
        line += ` ${labels.needs}[${offer.needs.join(',')}]`;
    if (offer.reason !== undefined)
        line += `  ·${offer.reason}`;
    return line;
}

export function emptyOfferFold(): OfferFoldState {
    return { delivered: new Map(), schemas: new Set() };
}

function isEntry(value: ActionOffer | OfferEntry): value is OfferEntry {
    return typeof (value as OfferEntry).offer === 'object' && (value as OfferEntry).offer !== null;
}

export function foldOfferRecord(state: OfferFoldState, record: Json): OfferFoldState {
    if (record === null || typeof record !== 'object' || Array.isArray(record))
        throw coded('OFFER_RECORD_INVALID');
    const raw = record as Readonly<Record<string, Json>>;
    if (raw.expired !== undefined && !Array.isArray(raw.expired))
        throw coded('OFFER_RECORD_INVALID');
    if (raw.added !== undefined && !Array.isArray(raw.added))
        throw coded('OFFER_RECORD_INVALID');
    if (raw.schemas !== undefined && !Array.isArray(raw.schemas))
        throw coded('OFFER_RECORD_INVALID');
    const delivered = new Map(state.delivered);
    for (const key of raw.expired ?? [])
        if (typeof key === 'string')
            delivered.delete(key);
    for (const item of raw.added ?? []) {
        const entry = isEntry(item as ActionOffer | OfferEntry) ? item as OfferEntry : offerEntry(item as ActionOffer);
        delivered.set(offerKey(entry.offer), entry);
    }
    const schemas = new Set(state.schemas);
    for (const digest of raw.schemas ?? [])
        if (typeof digest === 'string')
            schemas.add(digest);
    return { delivered, schemas };
}

export function foldOffers(records: readonly Json[]): OfferFoldState {
    return records.reduce(foldOfferRecord, emptyOfferFold());
}

/** added 含 changed；expired 只含已不再有效的 key；被预算收起但仍有效的进入 folded。 */
export function diffOffers(delivered: OfferFoldState, valid: readonly OfferEntry[], deliver: readonly OfferEntry[]): OfferDelta {
    const validKeys = new Set(valid.map((entry) => offerKey(entry.offer)));
    const deliverKeys = new Set(deliver.map((entry) => offerKey(entry.offer)));
    const added: OfferEntry[] = [];
    const changed: OfferEntry[] = [];
    for (const entry of deliver) {
        const previous = delivered.delivered.get(offerKey(entry.offer));
        if (!previous) {
            added.push(entry);
            continue;
        }
        if (previous.digest !== entry.digest) {
            added.push(entry);
            changed.push(entry);
        }
    }
    const expired: string[] = [];
    const folded: string[] = [];
    for (const key of delivered.delivered.keys()) {
        if (!validKeys.has(key))
            expired.push(key);
        else if (!deliverKeys.has(key))
            folded.push(key);
    }
    const schemas: ActionSchemaCard[] = [];
    const emitted = new Set<string>();
    for (const entry of added) {
        const card = entry.schema;
        if (!card || delivered.schemas.has(card.digest) || emitted.has(card.digest))
            continue;
        emitted.add(card.digest);
        schemas.push(card);
    }
    return {
        initial: delivered.delivered.size === 0,
        added: added.map((entry) => entry.offer),
        entries: added,
        changed: changed.map((entry) => entry.offer),
        expired: [...expired].sort(compareText),
        folded: [...folded].sort(compareText),
        schemas,
        omitted: [],
    };
}

/** 送达后更新 fold；expired 从已送达集合移除，新增（含 changed）覆盖旧值，schema 摘要记账。 */
export function applyOfferDelta(state: OfferFoldState, delta: OfferDelta): OfferFoldState {
    const delivered = new Map(state.delivered);
    for (const key of delta.expired)
        delivered.delete(key);
    for (const entry of delta.entries)
        delivered.set(offerKey(entry.offer), entry);
    const schemas = new Set(state.schemas);
    for (const card of delta.schemas)
        schemas.add(card.digest);
    return { delivered, schemas };
}

function keyLine(key: string): string {
    const index = key.indexOf('@');
    if (index < 0)
        return key;
    const target = key.slice(index + 1);
    return target === '-' ? key.slice(0, index) : `${key.slice(0, index)} ${target}`;
}

export interface OfferRenderOptions {
    readonly labels?: Partial<OfferLabels>;
}

/**
 * 渲染一段“可用行动”。没有变化时返回空串，调用方不写空段。
 * schema 行在前，接着是 + / - 行，最后是 omitted 汇总。
 */
export function renderOfferDelta(delta: OfferDelta, options: OfferRenderOptions = {}): string {
    const labels = { ...DEFAULT_OFFER_LABELS, ...options.labels };
    const lines: string[] = [];
    for (const card of delta.schemas)
        lines.push(schemaLine(card));
    const entries = delta.entries.length ? delta.entries : delta.added.map((offer) => offerEntry(offer));
    for (const entry of entries)
        lines.push(offerLine(entry, labels));
    for (const key of delta.expired)
        lines.push(`- ${keyLine(key)}`);
    if (delta.omitted.length) {
        const summary = delta.omitted.map((item) => `${item.action} ×${item.count}`).join(', ');
        lines.push(`${labels.omitted}：${summary}（完整清单 ${labels.listPath}）`);
    }
    if (!lines.length)
        return '';
    return `${delta.initial ? labels.full : labels.change}\n${lines.join('\n')}`;
}

export interface OfferTruncation {
    readonly deliver: readonly OfferEntry[];
    readonly omitted: readonly OmittedOffer[];
}

/**
 * 按 maxOffers 与 maxOfferBytes 整条取舍；schema 行只在尚未送达时计入。
 * 字节预算不会切开单条记录：放不下就整条收起。
 */
export function truncateOffers(entries: readonly OfferEntry[], limits: OfferLimits, sentSchemas: ReadonlySet<string> = new Set()): OfferTruncation {
    const deliver: OfferEntry[] = [];
    const products: OfferEntry[] = [];
    let bytes = 0;
    for (const entry of entries) {
        if (deliver.length >= limits.maxOffers) {
            products.push(entry);
            continue;
        }
        let size = Buffer.byteLength(offerLine(entry));
        if (entry.schema && !sentSchemas.has(entry.schema.digest))
            size += Buffer.byteLength(schemaLine(entry.schema)) + 1;
        if (deliver.length)
            size += 1;
        if (bytes + size > limits.maxOfferBytes) {
            products.push(entry);
            continue;
        }
        bytes += size;
        deliver.push(entry);
    }
    const counts = new Map<string, number>();
    for (const entry of products)
        counts.set(entry.offer.action, (counts.get(entry.offer.action) ?? 0) + 1);
    const omitted = [...counts.entries()].sort(([left], [right]) => compareText(left, right)).map(([action, count]) => ({ action, count }));
    return { deliver, omitted };
}

/** actions 是第一个接入 DeliveryFold 的通道；state/percepts 由 C3 按同一契约实现。 */
export const actionsDeliveryChannel: DeliveryChannel = {
    name: 'actions',
    field: 'offers',
    empty: emptyOfferFold(),
    fold: (state, record) => foldOfferRecord(state as OfferFoldState, record),
    diff: (delivered, valid, deliver) => diffOffers(delivered as OfferFoldState, valid as unknown as readonly OfferEntry[], deliver as unknown as readonly OfferEntry[]),
    render: (delta) => renderOfferDelta(delta as OfferDelta),
};
