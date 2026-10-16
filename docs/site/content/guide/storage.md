# 管理事务、完整点与运行分支

运行目录同时保存当前事实、完整步骤和依赖工件。先完成[快速开始](../start/quickstart.md)，本页使用它的两步规则运行演示恢复与只读检查。修改领域事实的事务由机制拥有，完整步骤交给 Runtime 发布。

## 一、检查可用的完整点

```bash
uv run python -m society0.kernel.observation runs/hello/run
```

检查 complete、failed 和 complete_step_lower_bound，再按需要追踪原始结果及 Thread。单次 SQL 事务提交后，其他业务读者可见当前事实；完整步骤还要等待业务阶段、回调、结果与必要工件全部收束。失败步骤中的已提交事实保留用于诊断。

领域写入在同一个规范事务中维护原始事实和当前投影。例如保存成交时同时更新库存和余额，再返回行动结果。下面是机制方法内的接线片段，表与参数由该领域定义：

```python
def record_transfer(store, sender, receiver, amount):
    def write(writer):
        writer.execute("INSERT INTO transfers(sender,receiver,amount) VALUES(?,?,?)",
                       (sender, receiver, amount))
        writer.execute("UPDATE balances SET amount=amount-? WHERE actor=?", (amount, sender))
        writer.execute("UPDATE balances SET amount=amount+? WHERE actor=?", (amount, receiver))
    store.transaction(write)
```

真实转账处理器还需在同一写入边界核对业务资格与余额。事务之外的网络效果、模型请求及已经发出的通知有各自事实边界，恢复本地数据不会撤销外部效果。

## 二、从完整步骤继续

从快速开始的第 1 步创建新运行，配置继续保留完整时间范围：

```bash
uv run python -m society0.kernel.runner \
  --factory examples.core_next.rule_run:build \
  --config runs/hello/config.json \
  --source runs/hello/run --step 1 \
  --output runs/hello/storage-branch
uv run python -m society0.kernel.observation runs/hello/storage-branch
```

新分支应执行剩余第 2 步，拥有新的 run_id 并记录来源；原运行继续保留。恢复会验证 schema，安装服务并跳过初始化。若来源数据已经受到错误规则影响，选择首次影响之前的可信完整点；起始设定改变时重新初始化。

## 三、准备只读分析与独立导出

分析固定时点时，用下面完整脚本建立临时只读视图。它需要源运行第 1 步已经完成。

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

重复分页时复用同一个视图。准备视图会复制或恢复所需状态，占用临时数据库和工件空间；实时观察使用 live 入口，注明其可能包含未完成步骤。

需要独立转移某个完整点时，使用 `StageStore.restore(source, destination, step=...)` 创建新 root，关闭返回的写入器后核对目标读取。目标保留选中点已有的业务与 Thread 历史以及完整链所声明的文件依赖；源中更早的逐步恢复身份留在源归档。若要继续执行，优先使用本页的 runner 恢复命令，它同时保存正式运行合同。

## 四、清理前留证

停止本次运行的写入者并保存诊断资料后，才考虑显式 `StageStore.collect_orphans(path)`。它取得独占锁、检查完整链与依赖，清理 artifacts/changesets 中未被完整身份引用的文件。失败尝试遗留的未发布正文可能在此范围内，清理前先决定哪些诊断需要保留。

运行中持有 writer 锁会拒绝清理；完整身份引用的旧工件继续保留。恢复链缩短、文件复制和历史数据量各自有不同成本，迁移前检查实际存储空间。具体接口见[存储参考](../reference/storage.md)和[生命周期参考](../reference/storage-lifecycle.md)，日常故障按[排查指南](troubleshooting.md)处理。
