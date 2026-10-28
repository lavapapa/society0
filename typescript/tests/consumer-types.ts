import { createHost, Context, Service, FiberState, type BootSeed } from '@society0/host';
import { Context as AbiContext } from '@society0/host/abi';
import { Context as NativeContext } from '@society0/cordis';
import { getSourceIdentity } from '@society0/host/source';
import { Entries } from '@fixtures/provider/service';
import '@fixtures/consumer';
import '@fixtures/jsdoc';
const native: NativeContext = new Context();
const publicContext: Context = native;
const abi: AbiContext = publicContext;
const answer: number = abi.entries.answer(21);
// @ts-expect-error The augmentation must retain the actual method signature.
abi.entries.answer('invalid');
const service: Service = new Entries(native);
const seed: BootSeed = { boot: [] };
void [FiberState.ACTIVE, answer, service, seed, getSourceIdentity(), createHost];
