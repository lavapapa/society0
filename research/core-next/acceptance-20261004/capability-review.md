# 本轮能力复审

本报告核对重构后的全部 78 项能力与当前实际测试入口，并记录本轮执行证据的适用范围。审查者为独立 GPT‑6.1 Sol，参与编写认证、计算和计划调度三个消费者测试文件，未参与产品修复。产品与测试于 2026-10-04T06:59:13Z 冻结，最终形态为单 SQLite 共享块数据集。逐项编号、当前函数行号、最后执行结果、新合同消费者及后续门保存在 [逐项清单](capability-review.json)。

## 一、映射

以 [能力对照](../../../docs/core-next/capability-parity.md) 第 6.1 节的当前消费者为逐项入口，同时核对 [验收映射](../../../docs/core-next/acceptance-map.md) 和 [规模与覆盖](../../../docs/core-next/size-and-coverage.md) 的共同边界。当前 78 个编号均有实际源文件和可定位消费者，[最终 XML](full-final.xml) 中全部代表用例通过，缺失入口和缺失执行均为零。主体、机制与调度 A/B/E/S 共 23 项，模型、记忆与资源 L/M/R 共 27 项，文件、结果、持久化与观察 N/O/P/Q 共 23 项，研究入口与工作台 U 共 5 项，合计 78 项。

这一核对覆盖每个 ID 的当前消费者及主要责任模块。验收映射中的 A03–A07、L03–L12、M03–M10、R01–R05 等范围按包含两端的全部编号展开；早期 inventory 中的 23 个未映射编号来自范围解析遗漏，当前逐项清单没有缺失入口。文件及函数定位支持证据追踪，具体语义仍依据消费者中的业务值、行动顺序、原文、失败边界与恢复断言判断。

本轮新合同另列认证、原生计算、公开计划调度和成熟提供方适配消费者。78 项映射完整性与新增合同完整性分别保留，新增测试没有被折算成额外能力编号。

[最终覆盖率](coverage-final.json) 记录全 society0 的 8,077 条语句，其中 7,080 条执行、997 条未执行，语句覆盖率为 87.66%；分支为 1,927/2,514，即 76.65%，综合显示为 85.04%。kernel 语句为 5,958/6,412，即 92.92%，分支为 1,601/2,036，即 78.63%。[早期候选](coverage-final-candidate.json) 的 87.54% 是未开启分支采集的旧时点，另保留原身份。独立子进程与 Rust 不在父 Python 进程的这份采集范围中。logging 四个保留模块在父进程采集中零执行；test_shared_core_assets 的子进程实际构造和关闭 ExperimentLogContext，并检查没有旧主体模块依赖，完整日志写入及 hook 行为另有覆盖边界。复审识别的 native response_format 与注册 memory.recall 两个公共合同缺口，已由实际 SDK 请求和真实 Actions.invoke 消费者补入，最终全量通过。

## 二、证据

[本机最终全量](full-final.txt) 与 [XML](full-final.xml) 为 838 项通过、零 skip、15 项 real_e2e 明确排除，耗时 44.34 秒、退出码为零；其中 primary 为 763 项、experiments 为 75 项。[Linux 同源码全量](linux-linux-full-shared-final.txt) 同为 838 项通过、15 项排除、退出码为零，耗时 56.37 秒。逐项 JSON 记录每个代表函数的参数化结果及所属文件通过数量；首帧与第二帧写入错误两参数均在最后 XML 中有实际通过记录。先前第四轮及两次名含 frozen 的中间候选分别保留原容器和执行身份。

[新合同专项](new-contracts-second.txt) 为 26 项通过。认证用实际 Authlib 换码与刷新、JoseRFC 签发和验证合成 ID token、离线 HTTP 传输，覆盖动态 client ID、PKCE、state、nonce、audience、权限、跨实例串行刷新、令牌旋转及错误保留原凭据；正式 login 使用真实标准库 HTTPServer 与线程、随机本地端口和真实回调，成功与失败均关闭监听。这里证明产品协议和本地用户入口，真实账户登录、订阅资格、额度与服务端政策另行验收。

计算消费者使用真实 ProcessPoolExecutor 的 spawn 工作进程和有限可释放屏障，证明两个工作进程可同时进入计算、取消调用后原生任务结束前仍持有许可、等待许可者取消不增加提交、Host 关闭等待原生任务完成。两个图消费者共享计算服务，权威修改在父进程 StageStore 中按规范写入，并从完整点恢复。新增图快照版本复核用例覆盖排队期间状态改变后的旧投影拒绝，逐项清单保留其当前执行位置。FixedStep 与 PhasedSchedule 通过实际 RunPlan/run_plan 运行，覆盖 prepare、阶段顺序、唯一完整边界、选定完整点分叉、失败不发布及失败后从可信完整点恢复。

模型消费者已经迁至成熟 SDK 的离线传输。普通 OpenAI Chat、Azure、Ollama 分别验证原请求、单层物理重试、工具回执续发与资源关闭；普通 Responses 和订阅 Responses 保留 encrypted_content、namespace、精确 arguments，经完整点恢复后重新发送。Anthropic 验证 thinking 签名和完整工具回执的原生内容块，Google 验证 thoughtSignature、函数回执、嵌入响应、连接错误重试及同步和异步客户端关闭。终态消费者直接检查完整成功前的工具片段没有执行，失败、截断、错误和缺少成功终态的 EOF 保留未完成或错误事实。[跨进程与原生专项](opaque-native-process.txt) 的 14 项通过包含新解释器 StageStore.restore、原 typed 消息逐值相等及实际 SDK 续发的签名、namespace、arguments 和工具回执精确断言，也包含排队图快照变更后的拒绝。

存储与运行窄修的回归保留空 changeset 的合法压缩帧、真实数据恢复、发布失败的原始异常和旧完整点、实际 SDK 的嵌入索引与逐原文向量对应、嵌套请求证据脱敏。Host 与 compose 使用单一资源拥有者任务配合 TaskGroup/AsyncExitStack；HTTP 使用真实 Uvicorn 套接字消费者，取消与原生异常同时发生时保留取消及原始 cause，许可在工作完成后归还，service.close 在查询排空后执行。相关独立专项为 [Host/compose](host-compose-owner-green.txt)、[终态与发布](terminal-index-publication-second.txt)、[HTTP 取消与异常专项](http-owned-worker-cause-second.txt)，其通过记录保留各自执行时点。HTTP 命名为 final 的前次工件保留红灯身份。

[实验组第二轮](experiments-second.txt) 的 72 项通过、1 个 sqlite-vec collection skip 保留早期身份；最后全量的 75 项实验已包含 sqlite-scalar、sqlite-vec0 与 Chroma 三种候选原向量距离消费者，零 skip。这些试验未改变生产默认记忆检索策略。[冻结源码正式 CLI](pilot-cli-final.txt) 新建 pilot 并完成两步，再由 workbench CLI 生成 [实际导出资料](pilot-workbench-final.json)，两入口退出码均为零；[这份新产物的 Node 消费者](node-pilot-frozen-chain.txt) 为 9 项通过、零 skip，包含主体、机制、会话与组件呈现；[最后单文件构建](node-build-final.txt) 成功、退出码为零。早期普通测试的 8 项通过/1 个 payload skip 和先前 pilot 专项保留原执行时点。

## 三、边界

真实服务门继续按 V05 判断，关联主体认知、行动循环、自动记忆、批量嵌入、真实资源限制、内置机制驱动及独立进程完整恢复。当前确定性 SDK 响应验证请求和响应协议；真实 endpoint 的模型可用性、预算、订阅资格、授权撤销和额度耗尽需要相应实际服务证据。15 项 deselected 保留排除身份。本轮 [ModelScope](free-smoke-modelscope.txt) 与 [GLM](free-smoke-glm.txt) 免费型号入口得到 401，独立 SIWC 账户尚待实际用户登录；它们没有形成真实行动、记忆及恢复成功证据。历史真实小请求和旧完整链各自保留原提交、运行合同与失败范围。

主体效果门继续按 V06 判断，关联 A03/A05、L01/L10、M03/M07、B02–B05、N01–N04 和 U02。当前消费者验证完整信息及行动权限可达，主体自主找到资料、取得完整原文、完成行动和研究判断仍需在当前提示、工具和模型合同下做效果对照。请求长度、工具数量和局部耗时分别作为资源观测。

规模门保留历史长度、活动量、单大对象、并发及完整步骤增长斜率的区分。本轮真实双进程屏障证明实际计算与取消许可的具体边界；最后大根测量证明冷工件完整性、独立页与有限范围、封存空间和有限投影步骤。完整产业阶段、模型与记忆、Chroma 驻留、WAL 长读及整进程组峰值仍按相应完整工作负载判断。

最终 [共享块大根](linux-performance-shared-final.json) 与 [旧大根](linux-performance-old.json) 使用同源 1,448,471 条记录，全部原值恢复相等，联合唯一物理分配均为 226,680,832 字节。相同 probe 内完整流程耗时为新 134.10 秒、旧 133.70 秒，差约 0.30%，属于单次试验的同量级结果，未证明整体加速。最终 import 为 59.69 秒、完整逐值 verify 为 74.36 秒、100 次独立小页为 0.0221 秒；峰值 RSS 为新 340,480,000 字节、旧 334,716,928 字节，且包含源巨值物化。保留操作系统缓存，Python fsync 计数未包括 SQLite 原生同步调用。

[最终运行投影](linux-performance-shared-runtime-final.txt) 的 1,000 次独立小页耗时 0.2127 秒，29,948,413 字节巨记录的 64 字节范围耗时 0.000373 秒。20 个固定 header 更新步骤的 Session 均为 2,416 字节，写入约 0.000169–0.000584 秒、完整发布约 0.000809–0.001412 秒，最后恢复通过。这提供固定投影的增量边界，完整业务增长斜率另按真实业务消费者判断。[跨页与随机范围](linux-performance-shared-access-final.txt) 另外遍历 1,000 页、42,213 条记录，并完成 3,000 次有限随机范围调用，范围原值相等。

中间 [seekable 候选](linux-performance-new.json) 的小页约 73 倍回退与 [逐记录帧候选](linux-performance-sqlite-final.json) 的约 3.73 倍物理空间退化保留为选型反证，均未用于最终容器的性能结论。当前仅一套 ChunkWriter 负责已有 64KiB 分块，SQLite 主键定位有限块；_open 内使用标准 functools.lru_cache(maxsize=1)，退出清空并关闭连接。[共享块消费者](sqlite-shared-complete.txt) 与 [错误专项](shared-sink-errors-final.txt) 保留实际 HTTP 新请求、页内共享块、零解压引用页、跨块原字节、原始写入错误及缓存释放的直接证据。

平台与发行门关联 N04、P/Q 组和 U05。[最终八组 macOS wheel 消费者](clean-consumers-shared-final.json) 全部退出码为零，实际导入来自隔离环境 site-packages；基础消费者执行公开 RunPlan 两完整步、新目录恢复、Dataset 巨原文与 None 恢复，并确认未安装 pyzstd 与模型 SDK。[Linux 最终基础 wheel](linux-linux-final-wheel-consumers.json) 同样完成公开消费者；原生扩展实际装载与 Linux 最后全量共同保留指定平台证据。正式提交、公开包版本、锁依赖、公开说明和部署身份继续由发行阶段共同核对；Windows 不在当前 POSIX 文件锁合同中。

## 四、结论

非作者复审未发现当前冻结产品的新阻断问题。全部 78 个能力 ID 的当前代表消费者在最后全量通过，macOS/Linux 同为 838 项通过，新协议、真实进程、HTTP、完整发布及恢复有实际产品消费者。迁移保留业务原值、完整消息、行动权限和失败传播，性能选型依据真实反证修正，没有为凑绿裁剪信息、降低行动空间或新建调度器。两套被拒容器的中间执行身份与最后共享块统计分开。

V07 的软件跨插件故障与独立审查范围已完成：[真实 Runtime/Actions 故障消费者](../../../tests/primary/test_kernel_final_boundaries.py) 在 partial write 后捕获同步、异步、取消或错误返回，仍阻止下一动作及完整发布；[Thread 原子边界](../../../tests/primary/test_kernel_threads_review.py)、[Memory 失败与索引恢复](../../../tests/primary/test_kernel_memory.py)、[组合生命周期](../../../tests/primary/test_kernel_composition_review.py)、[HTTP 取消与原生错误](../../../tests/primary/test_kernel_observation_http.py) 与授权游标消费者承接其余窗口，并在最后全量通过。冻结后非产品作者审查已核实际读取、完整点和成熟资源所有权。V08 依赖的真实服务与主体效果门仍保持未完成身份。

剩余最高风险为真实服务与账户尚未形成当前端到端成功证据、主体自主取得信息并完成行动的效果对照尚待完成，以及完整业务阶段的资源与长期增长斜率尚未由冷工件和有限投影专项覆盖。正式发行身份另按提交、包和部署流程确认。这些边界继续保留在逐项清单；当前确定性与指定平台验收结论可用于代码交付判断，真实研究与长期运行结论需要其对应门。

本报告没有运行测试、安装依赖、调用模型或读取真实凭据，全部执行结果来自主测试智能体留下的本轮工件。证据边界服务于是否能够交付和开展正式实验的判断；通过数量与能力映射分别报告。
