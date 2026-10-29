import type { PluginMetadata, ApiEntry } from './index.js';
export function hostMetadata(title: string, summary: string, configPolicy: PluginMetadata['configPolicy'] = [], allowedScopes: PluginMetadata['allowedScopes'] = ['host']): PluginMetadata {
    return { title, summary, configVersion: 1, configPolicy, scope: 'host', allowedScopes, hostAbi: 'society0/host/1' };
}
export function hostApi(exportPath: string, provides: readonly string[], requires: readonly string[]): ApiEntry {
    const service = (key: string) => ({ key, protocol: key, major: 1, capabilities: [] });
    return { exportPath, provides: provides.map(service), requires: requires.map(key => ({ service: service(key), optional: false, phase: 'module' })) };
}
