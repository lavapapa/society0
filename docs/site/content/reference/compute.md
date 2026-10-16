# ProcessCompute 与进程计算池

`from society0.kernel.compute import ProcessCompute` 提供单运行共享的进程计算池；组合时使用 `from society0.plugins import compute_plugin`，机制在安装函数里显式依赖该服务。池归 compute_plugin 所有，直接构造 ProcessCompute 的调用者要自己关闭它。

## 配置与提交

进程池用于可序列化的独立 CPU 任务，规范状态写入留在拥有者进程。

<!-- api: society0.kernel.compute.ProcessCompute -->
<!-- api: society0.kernel.compute.compute_plugin -->
<!-- api: society0.kernel.compute.ProcessCompute.run -->

```python
plugin = compute_plugin(max_workers=2, max_pending=4)
# 机制安装函数声明 requires=('compute',)，取得共享服务：
# compute = ctx.require('compute', 'compute')
# derived = await compute.run(top_level_function, compact_input)
```

max_workers 与 max_pending 均要求正整数，bool 或非正值抛 `ValueError`。前者限制工作进程数，后者限制已提交且实际未完成的任务数；两者表达不同资源。服务使用 spawn，函数应在可导入模块顶层定义，参数与返回值须可被进程序列化。

一次 run 提交一个函数任务，返回该函数的结果或传播执行异常。单个整图任务使用一个 worker；多个独立调用才能并行占用多个 worker。业务拆分、结果排序与冲突判断由机制负责。数据库连接、Runtime、Writer、ReadView 与可写共享状态不得传入进程任务。

建议先在短读回调中取得紧凑输入及依赖版本，退出读作用域后 await run；返回后重新核对版本，再按领域顺序用规范事务安装结果。版本不一致时采用领域明确的重算或拒绝策略，避免覆盖期间已发生的事实。

## 生命周期

<!-- api: society0.kernel.compute.ProcessCompute.close -->

首次 run 绑定当前事件循环，之后跨循环使用抛 `RuntimeError`。关闭后的服务拒绝提交；close 幂等，执行标准进程池 shutdown，取消尚未开始的任务并等待运行任务真正结束。

取消等待者不保证终止已运行的进程函数，其额度在原生 future 实际结束后归还。关闭可能等待长计算并阻塞当前调用线程，应将任务时长纳入退出预算。持有服务的插件应让调用任务先收束，再释放其依赖；进程函数的外部副作用由函数自身负责。

## 成本

容量限制控制任务数，输入字节与派生输出需另外计量。

调用者在等待额度前已构造的参数仍驻留内存；max_pending 不限制这部分，也不限制单任务序列化体积。整图传输承担完整输入、进程序列化、派生图与输出返回的成本。尽量传紧凑批次或明确不可变引用，共享同一池，避免按主体或机制重复创建进程池。

CPU 进程池与模型请求并发属于不同资源；请求等待应由提供方服务的额度控制。相关用法见[并行指南](<../guide/parallel.md>)；源码见 [ProcessCompute.run](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/compute.py#L22>)。
