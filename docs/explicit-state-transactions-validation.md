# 显式状态事务验证记录

日期：2026-08-14（Asia/Singapore）

基线：Society0 `b3ba8e4`；产业链 `4bce5ee`。默认对照模式为
`transparent_proxy`，新模式为 `explicit_transactions`。所有候选输出都写入新建的
`.artifacts/explicit-tx-validation`，没有读取或改写正式实验的运行目录。

## 1. Society0 语义与内置环境

显式事务小型测试共 20 项，覆盖递归只读、跨字段提交、读己写、异常零修改、完整记录
校验、append-only map/list、mixed entity、列表切片、`setdefault`、并发冲突、无冲突追加、
过期视图、恢复和 fork。Round Robin、Social Network 和 Plain 的显式模式测试共 5 项，
覆盖透明模式结果对照、批量消息/互动写入、事务内读己写和业务异常回滚。

最终离线全套命令：

```bash
/usr/bin/time -l env PYTHONPATH=src \
  <baseline-society0-venv>/bin/python -m pytest \
  tests/primary tests/e2e -q --disable-warnings --maxfail=1
```

结果：420 项通过，13 项真实端点测试按既有条件跳过；墙钟 72.44 秒；最大 RSS
201,965,568 bytes。默认透明模式的 Society0 E2E、Social 推荐和持久化回归均在该集合中，
因此旧模式保持原行为的结论来自同一份修改后代码，而非只来自改造前基线。

只读热路径另用同一锂电状态做 5 轮、每轮 20 万次循环（每次读取 5 个深层叶值）。中位数：

| 读取方式 | 中位耗时 | 相对普通 dict |
| --- | ---: | ---: |
| 普通 dict | 0.0322 秒 | 1.00x |
| `transparent_proxy` | 0.1706 秒 | 5.30x |
| `explicit_transactions` 只读视图 | 0.2904 秒 | 9.02x |

只读视图不复制记录，约 0.29 微秒/叶值；它仍比当前代理读取慢约 1.70 倍，原因是每层都要
检查 Tick lease 是否仍有效。这是保留生命周期安全后的实际成本，不把它描述成零开销。

## 2. 通用产业链复杂状态

真实生猪 fixture 包含 Facility、Inventory、ResourceAllocation、运行中的 Production、
嵌套 append history 和投影。透明/显式两路运行 7 个自然日后，关键根、生产进度和时钟一致；
显式模式在第 3 日注入后置异常时，canonical state 只保留前两日，失败日的生产、分配和
探针写入均未出现。该组 2 项测试墙钟 2.08 秒，最大 RSS 159,367,168 bytes。

产业链最终离线全仓回归为 630 项通过、17 项按既有条件跳过；墙钟 38.97 秒；最大 RSS
299,335,680 bytes。该轮在产业链新 worktree 的隔离虚拟环境中，从本地 Git 安装精确的
Society0 提交，依赖身份门禁也包含在全绿结果中。

Action 层额外覆盖直接消息、延迟通知、失败零修改、Agenda 返回值脱离事务，以及完整 Agent
Tick 的日度写入和 Inbox watermark。锂电首次候选据此发现并修复了 Action 返回事务视图的
生命周期问题；修复后的回归为 4 项通过。

## 3. 同一 checkpoint 三路结果与性能

三路都从同一个新建锂电 `complete_step_v4` step 0 恢复，使用相同配置、同一 62 主体状态，
运行 2026-05-01 至 2026-05-07。最终经济状态摘要均为
`a3163dfb897c78af80f21edd8d06e6b4a5ef40325e485ff952d436867fb8ac05`，逐根 JSON 结果一致。

| 模式 | 墙钟 | 进程内峰值 RSS | RSS 增量 | replacement | append | 总 delta |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 普通 dict | 7.274 秒 | 123,109,376 | 557,056 | 无 | 无 | 无 |
| `transparent_proxy` | 13.390 秒 | 164,413,440 | 41,631,744 | 14,705 | 22,183 | 36,888 |
| `explicit_transactions` | 37.737 秒 | 184,369,152 | 70,778,880 | 14,672 | 22,183 | 36,855 |

显式模式把同一事务内对同一记录的多次字段写合并，所以 replacement 比代理少 33 条，追加
事实数量相同。显式模式总耗时是代理的 2.82 倍；主要成本来自整日大事务的 overlay、最终
实体校验和提交计划。它换来了日级失败零修改，目前不适合作为纯性能默认值。

## 4. 恢复、分叉和历史增长

显式模式持久化/恢复/fork 及失败事务恢复共 2 项通过，墙钟 0.57 秒，最大 RSS
56,770,560 bytes。Memory visibility、receipt recovery、Agent Thread manifest 和 runtime
checkpoint 共 33 项通过，墙钟 2.78 秒，最大 RSS 163,332,096 bytes。

历史增长 benchmark 在 100、1,000、10,000 条既有历史上各运行 50 Tick：两种模式每 Tick
中位数始终为 6 条 delta、约 1,737 bytes、历史读取为 0。1000 Tick 长跑结果：

| 模式 | mutation 中位数 | publish 中位数 | delta/Tick | RSS 增量 |
| --- | ---: | ---: | ---: | ---: |
| `transparent_proxy` | 214,916.5 ns | 671,646 ns | 6 / 1,743 bytes | 3,047,424 bytes |
| `explicit_transactions` | 314,959 ns | 665,749.5 ns | 6 / 1,743 bytes | 3,178,496 bytes |

因此 publish 成本和写入规模没有随已有历史增长；恢复仍需从 root 沿 manifest 链重放，事务
模式没有消除长历史恢复的读取放大。

## 5. 真实 LLM、Embedding、Memory 与锂电候选

使用已有本地转发到测试模型服务，显式模式 E2E 完成 Environment 事务、Agent 事务、真实
LLM 指令、closed Agent Thread、真实 Embedding 写入和新会话 Memory 检索，再发布并恢复
checkpoint。结果 1 项通过；墙钟 5.00 秒；最大 RSS 156,106,752 bytes。测试结束后本地转发
已停止，没有修改或重启远端服务。

锂电 Provider runner 的离线 composition/resume/retry/fork 测试 21 项通过，墙钟 20.16 秒。
最终候选运行使用 `explicit_transactions`、免税反事实中心情景，从 2026-05-01 运行一个
Society0 step 至 2026-05-07：

- 状态 `complete`，20/20 主体成功，0 失败；20 条 Agent Thread 全部关闭；
- 98 次物理 Provider/Embedding 请求，819,772 tokens，0 provider failure，0 usage 缺失；
- 墙钟 116.06 秒，最大 RSS 351,911,936 bytes；
- execution identity 明确记录 `state_access_mode=explicit_transactions`；
- 全目录检索没有 `state transaction has expired` 或 `Error calling action`。

首次候选虽写出 `complete`，日志包含失效事务返回值，因此没有被计为通过；修复并使用全新
目录重跑后才形成上述结论。该证据只覆盖一个 7 日候选 step，不等于多年度正式消费税实验。

## 残余风险

- 显式整日事务的写密集成本仍显著高于代理模式；默认模式继续是 `transparent_proxy`。
- World JSON 是唯一事务资源。Memory、Thread、provider、文件、网络和 Social embedding
  flush 继续使用各自提交边界；领域通知已延迟到 World commit 后。
- 显式模式把旧的 Agent/Env 直接写法变成只读错误；自定义 Behavior/Action 必须迁移到
  `write_transaction()`。环境事务和 Agent 状态事务不嵌套，跨根原子写需要重新划定为一个
  World 根或提交后动作。
- 事务是同步上下文能力，不支持在另一个 `contextvars.Context` 中提交；异步外部调用不应
  放在事务体内。
- 历史恢复仍按 checkpoint 链读取；本工作只证明增量发布不扫描历史。
