import { Context, type TaskPromise, type ResourceReport, type HostCloseReport, createHost } from '@society0/host';
import Resources from '@society0/host/resources';
import Tasks from '@society0/host/tasks';
import { DiagnosticSink } from '@society0/host/diagnostics';
const plugin = { inject: ['resources','tasks'], async apply(ctx: Context) {
  const resource=await ctx.resources.open(()=>({value:42,close(){}}),'typed-resource');
  const value: number=resource.value;
  const task: TaskPromise<number>=ctx.tasks.run('typed-task',async signal=>{signal.throwIfAborted();return value},{bytes:8});
  await task.settled;
  const actual: number=await task;
  const report: ResourceReport=ctx.resources.closeReport();
  ctx.diagnostics.report({code:'TYPE_PROBE',message:String(actual+report.open),phase:'task'});
  await resource.close();
}};
async function bootstrap() {
  const host=await createHost({boot:[]});
  await host.ctx.plugin(Resources);await host.ctx.plugin(Tasks,{concurrency:1,maxPending:1,maxBytes:8});
  await host.ctx.plugin(plugin);
  const report: HostCloseReport=await host.close({timeoutMs:1000});
  return report.drained;
}
void bootstrap;void DiagnosticSink;
