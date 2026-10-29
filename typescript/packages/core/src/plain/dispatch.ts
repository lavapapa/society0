import type { Context } from '@society0/host';
import type { Json } from '../contracts/common.js';
import type { EventHandler, EventHandlers } from '../runtime-events/index.js';
import { ruleHandlerSlot } from '../driver-rule/index.js';
import { fail } from '../information/page.js';

function isRecord(value: Json | undefined): value is { readonly [key: string]: Json } {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function updatePayload(value: Json | undefined) {
    if (!isRecord(value)) return fail('PLAIN_UPDATE_PAYLOAD');
    if (typeof value.actorId !== 'string' || !value.actorId || value.attributes === undefined) return fail('PLAIN_UPDATE_PAYLOAD');
    return { actorId: value.actorId, attributes: value.attributes };
}

export function registerPlainDispatch(ctx: Context) {
    ctx.slots.contribute(ruleHandlerSlot, { id: 'plain/update', value: {
        async activate(input, context) {
            if (!isRecord(input)) return fail('PLAIN_UPDATE_INPUT');
            const payload = updatePayload(input.input);
            if (payload.actorId !== context.actorId) return fail('PLAIN_ACTOR_SCOPE');
            const receipt = await ctx.actions.execute('plain.update', { attributes: payload.attributes }, context);
            return { status: receipt.status === 'completed' ? 'completed' : receipt.status === 'waiting' ? 'waiting' : 'incomplete',
                value: receipt.value, evidence: JSON.parse(JSON.stringify(receipt)), requiredOperations: receipt.requiredOperations };
        }
    } });
    const handler: EventHandler = {
        id: 'plain.update',
        async execute(event, tx) {
            const payload = updatePayload(event.payload);
            if (!await ctx.actors.get(payload.actorId, tx)) return fail('PLAIN_ACTOR_MISSING');
        },
        activations(event) {
            const payload = updatePayload(event.payload);
            return [{ id: 'plain.update:' + JSON.stringify([event.id, payload.actorId]), actorId: payload.actorId,
                momentId: event.time, driver: 'rule', source: 'plain.update', input: event.payload }];
        }
    };
    ctx.inject(['eventHandlers'], scoped => {
        const events = scoped.get('eventHandlers') as EventHandlers;
        events.register(handler);
    });
}
