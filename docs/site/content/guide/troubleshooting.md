# 按失败边界排查问题

运行报错时，先别急着改配置：把原配置、异常和运行目录留住，判断错误落在安装、装配、主体激活还是完整步骤发布上。下面这些入口都用所选 Next 源码环境；要接着往下跑就另建新目录，别覆盖已有事实。

## 先分清是安装还是装配失败

在仓库根目录确认实际导入的是哪份代码、源码身份是什么：

```bash
git rev-parse HEAD
uv run python -c "import society0; print(society0.__file__); print(society0.__version__)"
```

路径指到另一份 checkout，就先把环境改对。基础规则能跑、模型或 Shell 却导入失败，按[安装页](../start/installation.md)补上对应的 extra；从源码构建原生文件系统时，检查指定的 Rust 工具链和目标平台。

| 报错边界 | 核对内容 | 下一步 |
| --- | --- | --- |
| duplicate plugin | 根插件与 includes 展开后的名字 | 删除重复声明或为独立实例重新命名 |
| missing plugin dependency | 消费者的直接 requires/schema_requires | 加入实际拥有者并声明正确的图 |
| dependency cycle | 服务图与数据图分别成环的位置 | 将人口数据与运行驱动分开，见[主体目录](actor-directory.md) |
| unresolved actor drivers | 来源数据的持久 driver 标签 | 提供对应工厂，核对恢复配置 |
| semantic recommendations require resources | 语义开关及两个资源引用 | 同时绑定 embedding 和 vector client |

安装失败可能发生在运行目录建出来之前，也可能已经留下初始化状态。保存异常栈，看一眼实际目录里有什么；下一次装配换一个新路径。

## 看运行进度和完成状态

```bash
uv run python -m society0.kernel.observation runs/study/run
```

把路径换成自己的运行。先看 `complete` 和 `failed`，再看 `progress`、`live_revision` 和 `complete_step_lower_bound`。`progress` 只是一份独立的诊断快照，已发布的范围要看完整描述符。模型在排队或等网络时，业务时间可能停着不动；用[观察教程](../tutorial/observation.md)找到对应的 Thread 和实际发出的物理请求。

提供方失败时，检查请求终态、端点、模型、授权和参数。不要靠无限重试来恢复；同一次物理尝试和它的错误已经记下来了。余额、资格或者不支持的参数都要去改实际配置，可信完整步骤之后的计算转到新运行里做。

## 主体看不到材料时

主体会返回 completed、waiting 或 incomplete。waiting 是一次有效的暂停；incomplete 默认让步骤失败，阶段显式选了 collect 时才保留原状态并收集。接着看具体的 reason、实际工具回执和有效预算：模型输出被截断、行动次数用尽，都属于决定没有完成。

主体说自己“没有看到材料”时，按这个顺序核对：

1. 用该主体和时点查一次 Information 根目录，确认路由已经挂上、discover 也获准。
2. 读数据集的元数据和全部页，核对授权之后的 total 和正文引用。
3. 看实际 Thread 输入、来源位置和认知插件绑定；新增消息为空，可能表示原文早就交付过了。
4. 检查记忆写入、召回和主动工具各自的策略，以及访谈的写入边界。

分页或范围读取报版本过期时，从一次新查询重新开始；续读要在同一个主体、时点和版本下读完。文件路径 `/world/...` 和 Information 原始路由路径的对应关系见[信息指南](information.md)。

## 恢复和收尾

schema 校验失败就核对源码和声明；目标路径里已经有东西就换新路径；writer 锁冲突就先确认是不是还有自己的写入进程在跑。原目录保持完整，不要直接删锁文件来绕开运行的所有权。

步骤已经发布、runner 最后却报失败时，看错误出在结果写入、完成发布还是后续诊断上，再用完整描述符判断范围。关闭很慢时，分清时间花在 Runtime 激活、worker 还是外部资源清理上；关闭还在等它自己拥有的任务时，把进度和异常证据留下来。

恢复起点由错误第一次进入主体认知或世界事实的那一步决定。只是展示转换出错，就从原始完整工件重新导出；业务规则改过，就重新判断历史还可不可信。恢复按[存储指南](storage.md)做，再用一小段区间核对结果和 Thread；大规模重跑之前，把这次核对的来源留着。
