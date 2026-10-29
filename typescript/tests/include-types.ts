import { applyEntryPatches, type PatchOptions } from '@society0/include';
import type { EntryOptions } from '@society0/loader';
interface ModelEntry {
  id: string;
  name: string;
  group?: boolean;
  config?: {weight:number} | ModelEntry[];
  isolate?: Record<string,string|boolean>;
}
const entries: ModelEntry[]=[{id:'one',name:'probe',isolate:{database:false},config:{weight:1}}];
const patches: PatchOptions<ModelEntry>[]=[{id:'one',isolate:{database:false}},{insert:[{id:'two',name:'probe',config:{weight:2}}]}];
const explicit: ModelEntry[]=applyEntryPatches<ModelEntry>(entries,patches,()=>{});
const inferred: ModelEntry[]=applyEntryPatches(entries,patches,()=>{});
// @ts-expect-error domain isolate retains its actual value type
const wrongLabel: PatchOptions<ModelEntry>={id:'one',isolate:{database:42}};
// @ts-expect-error config remains the model's structured value
const wrongConfig: PatchOptions<ModelEntry>={id:'one',config:{weight:'bad'}};
const native: EntryOptions[]=[{id:'native',name:'probe'}];
const legacy: PatchOptions[]=[{id:'native',disabled:true}];
const defaults: EntryOptions[]=applyEntryPatches(native,legacy,()=>{});
void explicit;void inferred;void wrongLabel;void wrongConfig;void defaults;
