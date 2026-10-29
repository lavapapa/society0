import { posix } from 'node:path';
import type { ExplorationMount, ExplorationNode } from '../contracts/exploration.js';
import type { DiscoveryScope, PageRequest } from '../contracts/data.js';
import type { SqliteReadView } from '../sqlite/contracts.js';
import type { MountRegistration } from './registry.js';
import { identity, after, page, fail } from '../information/page.js';
/** 根目录投影实际装配并可见的挂载，不填充未装配空间。 */
export class MountedRoot implements ExplorationMount {
    readonly id = 'exploration/root';
    readonly prefix = '/';
    constructor(private entries: readonly MountRegistration[], private registry: unknown) { }
    async visible() { return true; }
    async stat(path: string, scope: DiscoveryScope, view: SqliteReadView): Promise<ExplorationNode> { return { path: '/', objectId: JSON.stringify([view.runId, scope.actorId, 'mounted-root']), kind: 'directory', versions: [{ owner: 'database', version: view.revision }, ...this.entries.map(item => ({ owner: item.mount.prefix, version: item.registrationId }))] }; }
    async list(path: string, scope: DiscoveryScope, view: SqliteReadView, request: PageRequest) {
        const id = identity(scope, view, this.registry), last = after(request.cursor, id) ?? '';
        const nodes: ExplorationNode[] = [];
        for (const { mount } of this.entries) {
            if (posix.dirname(mount.prefix) !== '/')
                continue;
            if (await mount.visible(mount.prefix, scope, view))
                nodes.push(await mount.stat(mount.prefix, scope, view));
        }
        nodes.sort((a, b) => a.path < b.path ? -1 : 1);
        return page(nodes.filter(node => node.path > last), String(nodes.length), view.revision, request, id, node => node.path);
    }
    async open(): Promise<never> { return fail('EXPLORATION_NOT_FILE'); }
    async collect(): Promise<never> { return fail('EXPLORATION_NOT_COLLECTION'); }
}
