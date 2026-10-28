import { readFileSync } from 'node:fs';
export interface SourceIdentity {
  repository: string;
  commit: string;
  license: string;
  patches: readonly { order: number; file: string; purpose: string }[];
  dependencyLock: string;
  distributionTransform: string;
}
export function getSourceIdentity(): SourceIdentity {
  return JSON.parse(readFileSync(new URL('../SOURCE.json', import.meta.url), 'utf8'));
}
