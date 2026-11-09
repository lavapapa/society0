import { createPreflightSession } from '../packages.js';
import Schema from '@society0/schemastery';
import { hostMetadata, hostApi } from '../module-resolver/metadata.js';
import { Context, Service } from '@society0/cordis';
import type { ModuleLock } from '../module-resolver/index.js';
import { compileLayers, composeLayers, dumpPlan, type EntryPatch, type CompositionPlan, type ConfigEvidence } from './compiler.js';
export const Config = Schema.object({});
export const metadata = hostMetadata('profile-loader', 'Compile literal profiles and mount native child fibers');
export const api = hostApi('./profile-loader', ["profileLoader"], ["moduleResolver", "configResolvers", "diagnostics"]);
export * from './compiler.js';
export type { Profile } from './profile-input.js';
import { readLiteral, validateProfile, loadLayers, loadBindings, externalServices, type Profile } from './profile-input.js';
import type { ProfileAnalysis, ProfileProblem } from './types.js';
export { readLiteral } from './profile-input.js';
export { mountPlan, createMountSession } from './mount-session.js';
export type { MountedTree, MountSession } from './mount-session.js';
import { completeMount, createMountSession as createSession, type MountedTree, type MountSession } from './mount-session.js';
declare module '@society0/cordis' {
    interface Context {
        profileLoader: ProfileLoader;
    }
}
export class ProfileLoader extends Service {
    static Config = Config;
    static inject = ['moduleResolver', 'configResolvers', 'diagnostics'];
    private readonly mounted = new Set<MountedTree>();
    constructor(ctx: Context) { super(ctx, 'profileLoader'); }
    async compile(profile: Profile, options: {
        baseURL?: string;
        cliPatches?: readonly EntryPatch[];
    } = {}): Promise<CompositionPlan> {
        validateProfile(profile);
        const base = options.baseURL ?? this.ctx.baseUrl;
        if (!base)
            throw Error('PROFILE_BASE_URL_REQUIRED');
        const lock = await readLiteral(new URL(profile.lockfile, base).href) as unknown as ModuleLock;
        const layers = await loadLayers(this.ctx, profile, base, lock, options.cliPatches);
        const { tree } = composeLayers(layers);
        const session = createPreflightSession();
        const bindings = await loadBindings(this.ctx, tree, lock, session);
        return compileLayers(layers, bindings, { resolve: value => this.ctx.configResolvers.resolve(value), externalServices: externalServices(this.ctx, tree, bindings) });
    }
    /**
     * Tolerant composition check used by the platform entry. Collects missing
     * plugins/services/config problems instead of failing on the first fault.
     * Never mounts plugins and never opens a run directory.
     */
    async analyze(profile: Profile, options: {
        baseURL?: string;
        cliPatches?: readonly EntryPatch[];
    } = {}): Promise<ProfileAnalysis> {
        const missing: ProfileProblem[] = [], warnings: string[] = [];
        const empty: CompositionPlan = Object.freeze({ entries: Object.freeze([]), flat: Object.freeze([]), layers: Object.freeze([]) });
        const base = options.baseURL ?? this.ctx.baseUrl;
        if (!base)
            return { plan: empty, missing: [{ kind: 'config', id: 'profile/baseURL', hint: '无法确定 profile 的基准 URL', detail: 'PROFILE_BASE_URL_REQUIRED' }], warnings };
        try {
            validateProfile(profile);
            const lock = await readLiteral(new URL(profile.lockfile, base).href) as unknown as import('../module-resolver/index.js').ModuleLock;
            const layers = await loadLayers(this.ctx, profile, base, lock, options.cliPatches);
            const { tree } = composeLayers(layers);
            const session = createPreflightSession();
            const bindings = await loadBindings(this.ctx, tree, lock, session, missing);
            const plan = await compileLayers(layers, bindings, { resolve: value => this.ctx.configResolvers.resolve(value), externalServices: externalServices(this.ctx, tree, bindings), problems: missing });
            return { plan, missing, warnings };
        }
        catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            missing.push({ kind: 'config', id: 'profile', hint: `profile 无法装配：${message}`, detail: message });
            return { plan: empty, missing, warnings };
        }
    }

    async createMountSession(plan: CompositionPlan, ctx: Context): Promise<MountSession> {
        const session = await createSession(plan, ctx);
        const tree = session.tree;
        this.mounted.add(tree);
        tree.root.ctx.effect(() => () => { this.mounted.delete(tree); }, 'profile.inspection');
        return session;
    }
    async mount(plan: CompositionPlan, ctx: Context): Promise<MountedTree> { return completeMount(await this.createMountSession(plan, ctx)); }
    inspectConfig(id: string): ConfigEvidence {
        for (const tree of this.mounted) {
            const entry = tree.plan.flat.find(e => e.id === id);
            if (entry && tree.entries.has(id))
                return entry.evidence;
        }
        throw Error(`CONFIG_NOT_MOUNTED ${id}`);
    }
    trees(): readonly MountedTree[] { return [...this.mounted]; }
    dump(plan: CompositionPlan) { return dumpPlan(plan); }
}
export default ProfileLoader;
