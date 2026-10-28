import '@fixtures/provider/service';
/** @typedef {import('@society0/host').Context} Context */
export const inject = ['entries'];
/** @param {Context} ctx */
export function apply(ctx) {
  if (ctx.entries.answer(22) !== 44) throw new Error('wrong JSDoc service result');
  ctx.entries.register('jsdoc');
}
