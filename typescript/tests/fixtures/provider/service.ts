import { Service, type Context } from '@society0/host';
declare module '@society0/host' { interface Context { entries: Entries } }
export class Entries extends Service {
  readonly entries = new Map<string, number | null>();
  constructor(ctx: Context) { super(ctx, 'entries'); }
  register(id: string) {
    const owner = this.ctx.fiber.uid;
    return this.ctx.effect(() => {
      if (this.entries.has(id)) throw new Error(`duplicate ${id}`);
      this.entries.set(id, owner);
      return () => { this.entries.delete(id); };
    });
  }
  answer(input: number): number { return input * 2; }
}
