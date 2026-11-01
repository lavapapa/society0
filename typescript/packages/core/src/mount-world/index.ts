import {compareCodePoints} from '@society0/host/order';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({});
export const metadata: PluginMetadata = { "title": "mount-world", "summary": "将领域主体可见信息、集合和动作描述映射到/world", "configVersion": 1, "configPolicy": [], "scope": "generation", "allowedScopes": ["generation"], "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./mount-world", "provides": [], "requires": [{ "service": { "key": "exploration", "protocol": "exploration", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "information", "protocol": "information", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "actors", "protocol": "actors", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "actions", "protocol": "actions", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }] };
import type { Context } from '@society0/cordis';
import { randomUUID } from 'node:crypto';
import type { ExplorationMount, ExplorationNode } from '../contracts/exploration.js';
import { page, sized, after, identity, equal, fail, bytes } from '../information/page.js';
import '../exploration/index.js';
import '../information/index.js';
import '../actions/index.js';
import '../actors/index.js';
export default { Config, inject: ['exploration', 'information', 'actors', 'actions'], apply(ctx: Context) {
        const registration = randomUUID();
        const mount: ExplorationMount = { id: 'world/v1', prefix: '/world', visible: async (path, scope, reader) => {
                if (!await ctx.actors.get(scope.actorId, reader))
                    return false;
                if (path.startsWith('/world/information/')) {
                    try {
                        await ctx.information.describe(decodeURIComponent(path.slice('/world/information/'.length)), scope, reader);
                        return true;
                    }
                    catch (error) {
                        if ((error as any).code === 'INFORMATION_UNAVAILABLE')
                            return false;
                        throw error;
                    }
                }
                return ['/world', '/world/information', '/world/actions', '/world/self'].includes(path);
            }, stat: async (path, scope, reader) => {
                if (!await mount.visible(path, scope, reader))
                    return fail('WORLD_NOT_VISIBLE');
                let kind: ExplorationNode['kind'] = path === '/world' || path === '/world/information' ? 'directory' : path === '/world/self' ? 'file' : 'collection';
                let extra: any = {};
                let owner: string = registration;
                if (path === '/world/self') {
                    const actor = await ctx.actors.get(scope.actorId, reader);
                    extra = { rawBytes: String(bytes(actor)) };
                }
                if (path.startsWith('/world/information/')) {
                    const descriptor = await ctx.information.describe(decodeURIComponent(path.slice('/world/information/'.length)), scope, reader);
                    extra = { querySchema: descriptor.schema };
                    owner = descriptor.registrationId;
                }
                return { path, objectId: JSON.stringify([reader.runId, scope.actorId, path, owner]), kind, versions: [{ owner, version: reader.revision }, ...(path === '/world' || path === '/world/information' ? [await ctx.information.version()] : []), ...(path === '/world' || path === '/world/actions' ? [await ctx.actions.version()] : [])], ...extra };
            }, list: async (path, scope, reader, request) => {
                if (path === '/world') {
                    const id = identity(scope, reader, registration), last = after(request.cursor, id) ?? '';
                    const entries = await Promise.all(['/world/actions', '/world/information', '/world/self'].map(path => mount.stat(path, scope, reader)));
                    return page(entries.filter(node => compareCodePoints(node.path, last) > 0), '3', reader.revision, request, id, node => node.path);
                }
                if (path === '/world/information') {
                    const result = await ctx.information.discover(scope, reader, { ...request, maxResponseBytes: Math.floor(request.maxResponseBytes / 2) });
                    const items = await Promise.all(result.items.map(item => mount.stat('/world/information/' + encodeURIComponent(item.id), scope, reader)));
                    return sized({ ...result, items, responseBytes: 0 }, request.maxResponseBytes);
                }
                return fail('WORLD_NOT_DIRECTORY');
            }, open: async (node, scope, reader) => {
                if (!equal(await mount.stat(node.path, scope, reader), node))
                    return fail('WORLD_STALE');
                if (node.kind !== 'file')
                    return fail('WORLD_NOT_FILE');
                const raw = Buffer.from(JSON.stringify(await ctx.actors.get(scope.actorId, reader)));
                if (raw.length > 1048576)
                    return fail('WORLD_INLINE_BUDGET');
                let released = false;
                return { value: { node, read: async function* (range, signal) {
                            signal.throwIfAborted();
                            if (released)
                                return fail('WORLD_RELEASED');
                            const start = Number(range.offset);
                            if (!Number.isSafeInteger(start) || start < 0 || range.length > range.maxBytes || start + range.length > raw.length)
                                return fail('PAYLOAD_RANGE');
                            yield raw.subarray(start, start + range.length);
                        } }, release: async () => { released = true; } };
            }, collect: async (path, query, scope, reader, request) => {
                if (path === '/world/actions') {
                    const result = await ctx.actions.discover(scope, reader, { ...request, maxResponseBytes: Math.floor(request.maxResponseBytes / 2) });
                    return sized({ ...result, items: result.items.map(value => ({ kind: 'inline' as const, value: JSON.parse(JSON.stringify(value)) })), responseBytes: 0 }, request.maxResponseBytes);
                }
                if (path.startsWith('/world/information/'))
                    return ctx.information.query(decodeURIComponent(path.slice('/world/information/'.length)), query, scope, reader, request);
                return fail('WORLD_NOT_COLLECTION');
            } };
        ctx.exploration.register(mount);
    } };
