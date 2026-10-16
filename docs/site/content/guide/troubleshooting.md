# 按失败边界排查问题

先保留原配置、异常和运行目录，确定错误发生在安装、装配、主体激活还是完整步骤发布阶段。下面的入口均使用所选 Next 源码环境；需要继续运行时创建新目录，避免覆盖已有事实。

## 一、安装与装配

在仓库根目录确认实际导入路径和源码身份：

```bash
git rev-parse HEAD
uv run python -c "import society0; print(society0.__file__); print(society0.__version__)"
```

若路径指向另一份 checkout，先修正环境。基础规则成功但模型或 Shell 导入失败时，按[安装页](../start/installation.md)添加对应 extra；源码构建原生文件系统时检查指定 Rust 工具链与目标平台。

| 报错边界 | 核对内容 | 下一步 |
| --- | --- | --- |
| duplicate plugin | 根插件与 includes 展开后的名字 | 删除重复声明或为独立实例重新命名 |
| missing plugin dependency | 消费者的直接 requires/schema_requires | 加入实际拥有者并声明正确的图 |
| dependency cycle | 服务图与数据图分别成环的位置 | 将人口数据与运行驱动分开，见[主体目录](actor-directory.md) |
| unresolved actor drivers | 来源数据的持久 driver 标签 | 提供对应工厂，核对恢复配置 |
| semantic recommendations require resources | 语义开关及两个资源引用 | 同时绑定 embedding 和 vector client |

安装失败可能发生在运行目录创建之前，也可能已经留下初始化状态。保存异常栈并检查实际目录；下一次装配使用新路径。

## 二、运行进度与完成状态

```bash
uv run python -m society0.kernel.observation runs/study/run
```

把路径换成自己的运行。先看 complete 与 failed，再看 progress、live_revision 和 complete_step_lower_bound。progress 是独立诊断快照，完整描述符确认已发布范围。模型正在排队或等待网络时，业务时间可能保持不变；通过[观察教程](../tutorial/observation.md)定位相关 Thread 和实际物理请求。

若提供方失败，检查请求终态、端点、模型、授权和参数。不要把无限重试当作恢复策略；同一物理尝试及其错误已有记录。余额、资格或不支持的参数需要调整实际配置，可信完整点的后续计算转入新运行。

## 三、主体与资料

主体返回 completed、waiting 或 incomplete。waiting 是有效暂停；incomplete 默认使步骤失败，阶段显式选择 collect 时保留原状态并收集。检查具体 reason、实际工具回执及有效预算：模型输出截断和行动上限耗尽都属于决定未完成。

发现主体“没有看到材料”时，按以下顺序核对：

1. 用该主体和时点查询 Information 根目录，确认路由已挂载且 discover 获准。
2. 读取数据集元数据和全部页，核对授权后的 total 与正文引用。
3. 查看实际 Thread 输入、来源位置及认知插件绑定；空的新增消息可能表示已有原文已交付。
4. 检查记忆写入、召回、主动工具各自的策略，以及访谈的写入边界。

分页或范围读取报告版本过期时，从新查询开始；保持同一主体、时点与版本完成一次续读。文件路径 `/world/...` 与 Information 原始路由路径的对应关系见[信息指南](information.md)。

## 四、恢复与收尾

schema 校验失败时核对源码和声明；目标路径已有内容时选新路径；writer 锁冲突时确认是否仍有自己的写入进程。保持原目录完整，避免直接删除锁文件绕过运行所有权。

若步骤已发布但 runner 最后报告失败，检查错误发生于结果写入、完成发布还是后续诊断，依据完整描述符判断范围。关闭缓慢时区分 Runtime 激活、worker 与外部资源清理；关闭仍在等待自己拥有的工作时，保留进度与异常证据。

错误首次进入主体认知或世界事实的步骤决定恢复起点。展示转换出错时从原始完整工件重新导出；业务规则已改变时重新检查历史可信性。按[存储指南](storage.md)执行恢复，再用短区间核对结果和 Thread；大规模重跑前保留这次核对的来源。
