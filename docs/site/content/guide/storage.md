# 管理事务、完整步骤与运行分支

想接着一次运行往下跑、想在不碰原运行的前提下分析某个时点，或者准备清理磁盘时，先用观察接口看清有哪些可用的完整步骤。运行目录里同时存着当前事实、完整步骤和依赖工件；改动领域事实的事务由机制自己拥有，完整步骤交给 Runtime 发布。下面用[快速开始](../start/quickstart.md)那次两步规则运行演示恢复和只读检查。

## 先看有哪些可用的完整步骤

```bash
uv run python -m society0.kernel.observation runs/hello/run
```

先看 `complete`、`failed` 和 `complete_step_lower_bound`，再按需要追到原始结果和 Thread。一次 SQL 事务提交之后，别的业务读者就能看到当前事实；完整步骤还要等业务阶段、回调、结果和必要工件全部收束。失败步骤里已经提交的事实会留着供诊断。

领域写入在同一个规范事务里同时维护原始事实和当前投影：比如保存一笔成交时，把库存和余额一起更新，再返回行动结果。下面是机制方法里的装配片段，表和参数由该领域自己定义：

```python
def record_transfer(store, sender, receiver, amount):
    def write(writer):
        writer.execute("INSERT INTO transfers(sender,receiver,amount) VALUES(?,?,?)",
                       (sender, receiver, amount))
        writer.execute("UPDATE balances SET amount=amount-? WHERE actor=?", (amount, sender))
        writer.execute("UPDATE balances SET amount=amount+? WHERE actor=?", (amount, receiver))
    store.transaction(write)
```

真实的转账处理器还要在同一个写入边界上核对业务资格和余额。事务之外的网络效果、模型请求和已经发出去的通知各有各的事实边界，恢复本地数据不会把外部效果撤回来。

## 从第 1 步建一条新分支

从快速开始的第 1 步建一次新运行，配置里继续保留完整的时间范围：

```bash
uv run python -m society0.kernel.runner \
  --factory examples.core_next.rule_run:build \
  --config runs/hello/config.json \
  --source runs/hello/run --step 1 \
  --output runs/hello/storage-branch
uv run python -m society0.kernel.observation runs/hello/storage-branch
```

新分支应该只执行剩下的第 2 步，它有自己的 run_id，并记下来源；原运行原样留着。恢复会校验 schema、安装服务，然后跳过初始化。来源数据已经受了错误规则影响时，退到首次受影响之前的可信完整步骤；起始设定变了就重新初始化。

## 做只读分析或单独导出一个完整步骤

想在某个固定时点上做分析，用下面这段完整脚本建一个临时只读视图，前提是源运行的第 1 步已经完成。

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from society0.kernel.storage import StageStore
from society0.kernel.observation import Observation

with TemporaryDirectory() as directory:
    target = Path(directory) / "view"
    with StageStore.prepare_readonly("runs/hello/run", target, step=1):
        pass
    with Observation(target) as reader:
        print(reader.status())
```

分页要读很多次时复用同一个视图。准备视图会复制或恢复所需状态，占掉临时数据库和工件空间；实时观察走 live 入口，它可能包含尚未完成的步骤。

要把某个完整步骤单独转移出去，用 `StageStore.restore(source, destination, step=...)` 建一个新的 root，关掉返回的写入器之后再核对目标读取。目标里保留选中点已有的业务与 Thread 历史，以及完整链声明过的文件依赖；源里更早的逐步恢复身份仍然留在源归档。要继续往下执行，优先用这一页给的 runner 恢复命令，它会同时保存正式的 `RunContract`。

## 清理之前先留证据

先把这次运行的写入者停掉、诊断资料存好，再考虑显式调用 `StageStore.collect_orphans(path)`。它会取得独占锁、检查完整链和依赖，清掉 artifacts/changesets 里没有被完整身份引用的文件。失败尝试留下的未发布正文可能就在这个范围内，动手之前先想清楚哪些诊断要留着。

运行中还持有 writer 锁时，清理会被拒绝；完整身份引用到的旧工件继续留着。恢复链长短、文件复制和历史数据量各有各的开销，迁移前先看实际占了多少空间。具体接口见[存储参考](../reference/storage.md)和[生命周期参考](../reference/storage-lifecycle.md)，日常故障按[排查指南](troubleshooting.md)处理。
