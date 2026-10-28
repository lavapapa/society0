import type { Context } from '@society0/host';
import '@fixtures/provider/service';
export const inject = ['entries'];
export function apply(ctx: Context) {
  if (ctx.entries.answer(21) !== 42) throw new Error('wrong service result');
  ctx.entries.register('typescript');
}
