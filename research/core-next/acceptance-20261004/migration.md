# 本轮测试迁移复审

本报告按产品合同归组记录旧测试断言的承接方式，并区分旧 fixture 失配、实际产品故障和新增合同。比较范围为当前工作树相对本轮进入验收时的 Git 基线，上一轮未提交实现纳入本轮产品验收。最终产品与测试于 2026-10-04T06:59:13Z 冻结，macOS/Linux 最后全量同为 838 项通过、15 项真实端点排除。独立复审读取了源码差异、原断言、新消费者和主测试工件，没有执行测试或修改产品。全部 78 个能力 ID 的当前入口与执行边界另见 [能力复审](capability-review.md) 和 [逐项清单](capability-review.json)。

## 一、模型

旧 LLMManager/EmbeddingManager 私有 fixture 直接替换 manager.clients、_execute_request 和嵌入微批内部字段。产品现在由成熟 Model/EmbeddingModel 与提供方 SDK 拥有协议，测试通过 [provider_http](../../../tests/primary/provider_http.py) 在实际 SDK 的 HTTP 传输处替换响应；这些 helper 属于测试资产。原请求、请求默认值、strict/parallel/tool_choice、超时、物理重试、许可占用、工具回执和关闭断言继续由 [模型消费者](../../../tests/primary/test_kernel_models.py)、[嵌入消费者](../../../tests/primary/test_kernel_embedding_provider.py) 与 [共享资源消费者](../../../tests/primary/test_resource_managers_shared.py) 承接。公开错误现在归 ProviderFailure，底层 SDK 原因和物理错误事实保留其关联。

原共享资源九项测试保留混合 trust_env 连接池、SDK 零内部重试、嵌入合批并发、逐主体完整输入、超时和连接故障等语义。[共享资源迁移工件](shared-resource-migration.txt) 记录九项通过。plural metadata 由规范 embedding_use 记录保留原 metadata，sources 分别关联每个逻辑位置和物理 item_index；旧 metadata 深拷贝相等断言、20 个主体的独立原文、精确向量与单物理请求断言继续保留。客户端从惰性构造改为首次使用时启动，测试先启动再检查真实池容量，未请求过的服务按关闭状态判断生命周期。

opaque 测试从通用 ChatCompletion 的额外字段替身迁至 SDK 原生 typed 消息：[Responses 消费者](../../../tests/primary/test_kernel_provider_fields.py) 检查 encrypted_content、namespace、精确 arguments、工具回执，随后完整点恢复并在新解释器重新发送；[多提供方消费者](../../../tests/primary/test_kernel_multi_provider_acceptance.py) 补 Anthropic thinking 签名和 Google thoughtSignature。原“下一请求仍带原扩展内容”的行为获得实际受支持协议承接，通用 Chat 并未被冒称支持所有提供方扩展。普通 OpenAI、Responses、Azure、Ollama、Anthropic、Google 与订阅 profile 分别通过实际离线 SDK 通路核对，真实免费服务与账户门保持独立状态。

[首次终态红灯](chat-terminal-red.txt) 证明无 finish_reason 的 EOF 仍可能形成完整-looking 行动；产品采用 SDK 的 requires_finish_reason profile，新增 [公开终态消费者](../../../tests/primary/test_kernel_provider_terminals.py) 保证成功终态之前没有领域动作。[终态、索引和发布初次工件](terminal-index-publication-first.txt) 及 [修复后工件](terminal-index-publication-second.txt) 另外保留嵌入重复、缺失或错误 index 的诊断原响应、禁止错误缓存，以及完整发布失败原异常和可信旧完整点。请求证据使用既有递归脱敏函数处理副本，实际 wire、typed 原文和签名保持其协议内容。

## 二、存储

原 zlib 解压 fixture 与线程压缩器内部字段对应旧编码。当前正文采用原生同步 UTF‑8 编码和独立 zstd 帧，首个 sink 错误后停止外部写入，事务按原异常回滚；编码阶段没有后台压缩任务。原“线程池所有任务排空”断言迁为 [同步编码消费者](../../../tests/primary/test_kernel_compression.py) 的执行线程、无新增线程、固定 64KiB 帧、Unicode/数值/空值全值、sink 故障停止副作用、压缩错误后可用和独立恢复断言。[非作者编码消费者](../../../tests/primary/test_kernel_compression_review.py) 保留 KeyboardInterrupt 回滚、首帧时只消费一块、恢复正文逐值相等。空 changeset 原先产生零字节文件的故障由明确写入空内容生成合法 zstd 帧修复，新增连续空步与非空步混合恢复用例。

Dataset 最初从单 SQL 工件迁至记录目录 SQLite 与 seekable zstd 正文两个共同登记工件。原文件数量 1 的结构断言曾相应改为 2，原记录数、逐值相等、范围原字节、完整点恢复、脱源读取、正文共享、readonly 不产生 root 副本与 Session 增量界限保留。实际大根反证后，逐记录独立帧 SQLite 候选虽然修复小页，却显著扩大空间，随后撤回。当前实现为单 SQLite 共享块工件：records 按 ordinal 保存 raw_start/raw_bytes，blocks 按 id 索引标准独立 zstd 帧。ChunkWriter 提取原 write_chunks 中已有的 64KiB 分块职责，在线单值与冷批次连续 JSON 共用；Dataset 成功消费所有 rows 后才 finish，失败时保留原异常并关闭私有工件。文件数量恢复为 1，失败 attach 的孤儿数也按单工件修正。格式身份为 4，数据集头与引用取消 body_artifact，未增加格式兼容桥。

[真实大根对照](linux-performance-execution.json) 的 1,448,471 条记录恢复逐值相等，但 seekable 固定小页读取出现约 73 倍回退。正式 Results 页入口会新建 Datasets，ObservationService 的 HTTP 请求会新建 Observation；成熟库首次打开需解析全 seek 表。小型解压计数消费者未代表这一实际总代价，该红灯保留为真实选型反证。当前 [数据集审查](../../../tests/primary/test_kernel_datasets_review.py) 恢复跨记录共享帧合同，继续验证相邻记录原值、大正文跨帧、尾部范围及完整恢复；正文消费者直接计数实际 decode_chunk，巨对象末尾短范围仅解压目标帧。每次公开请求独立打开 SQLite，按 raw_start 直接换算有限块主键；_open 中使用标准 functools.lru_cache(maxsize=1) 复用一个解压块，finally 清空并关闭 connection。[最后大根](linux-performance-shared-final.json) 全部原值相等，物理分配与旧相同，probe 内完整流程新 134.10 秒对旧 133.70 秒，保留单次测量同量级结论；末尾范围与独立请求测量另有最终工件，未宣称整体加速。

两个 WorkBench 消费者原先要求千条小记录共享一帧、解压一次。逐记录候选曾改为 len(values) 帧，其 [前次全量](full-frozen.txt) 的 833 项通过、2 项共享帧断言失败，以及 [候选直接回归](sqlite-workbench-frames.txt) 分别保留原身份。当前共享块实现重新承接原一帧/一次 page 打开的断言，同时保留全部业务值逐值相等与原业务 payload_ref 字段完整。[公共 HTTP 专项](sqlite-public-http.txt) 的 11 项通过发生在逐记录候选上，真实套接字、新 Observation/Results/Datasets 请求、10/5,000 条无关历史对照、巨值引用页零解压和跨块原字节合同已在 [共享块组合](sqlite-shared-complete.txt) 和 [最后全量](full-final.xml) 重验。共享批次另用首/第二 emit 故障两参数证明原 OSError、禁止尾 flush/继续 producer、零登记与零残留、旧完整点恢复；缓存用例检查单作用域 currsize=1、退出清空及下一请求独立，见 [直接工件](shared-sink-errors-final.txt)，两参数也已纳入最后全量。

[10MiB 成本消费者](../../../tests/experiments/test_core_next_large_root_probe.py) 的旧 Session whole/split 十倍阈值在原生 zstd 下首次为 22,448 对 2,640 字节，即约 8.5 倍，见 [实验首次红灯](experiments.txt)。阈值改为 whole 大于 split，并增加同布局 10MiB 大于 1MiB 的捕获增长对照；这两项继续检查改写冷块的成本，避免把特定压缩格式的倍率当成业务合同。10MiB whole 本例已补 cold 原文、hot 修改与完整点恢复逐值相等，原 200KiB whole 负载、范围字节、changeset 小于 1KiB 和 disk 发布窗口断言继续保留。Session 的压缩块驻留与调用方完整 JSON 值物化分别解释，当前大根资源结论由专项测量承担。

## 三、生命周期

ActivationPool.start 现在由调用方提供成熟 TaskGroup；[共享激活池边界](../../../tests/primary/test_activation_pool_shared.py) 的原激活上限、剩余任务、idle 边界新增任务、drain/close 等待和 worker 完成断言继续保留。Host 与 compose 的 TaskGroup 跨任务进入/退出产生真实取消错误，修复为单资源拥有者任务配合 AsyncExitStack 与标准任务组，插件 quiesce 后逆序关闭。[Host/compose 修复工件](host-compose-owner-green.txt) 记录正常退出、安装失败、主动取消、重复外部取消和清理失败的直接组合通过，没有增加自制调度队列。

图源初始化已使用同步 json.load，原“异步线程读文件尚未结束时等待 native drain”的 fixture 对应结构已结束。[prepare 取消消费者](../../../tests/primary/test_kernel_preparation_review.py) 以实际插件 prepare 上下文中的可释放 await 保留取消时文件作用域、清理与未发布根断言，名称明确其 prepare 边界。新的 [计算消费者](../../../tests/primary/test_kernel_compute_acceptance.py) 用实际 spawn ProcessPoolExecutor 检查两个 worker 同时计算、取消后原生 Future 结束前的许可、等待者取消、Host 关闭排空、共享图消费者、父进程权威写入恢复以及等待期间快照变化拒绝。计算任务数量额度与输入字节成本继续分开。

HTTP 从旧同步 server fixture 转为 [真实 Uvicorn helper](../../../tests/primary/http_server.py)，标准 Server 仍拥有套接字及生命周期。原慢 body、饱和 503、断线槽归还、暂停大响应时其他 status 可读和最终字节预算断言保留。[原始取消红灯](http-raw-cancel-red.txt) 发现工作线程未结束即归还槽位的问题；产品用标准任务组等待同步 worker，异常与取消同时发生时保留 CancelledError 和原始 cause，lifespan 取得所有许可后关闭 service。[取消与异常修复工件](http-owned-worker-cause-second.txt) 为六项通过且无警告。graceful shutdown 测试中客户端可能收到关闭导致的非 JSON 响应，fixture 明确记录断线结果并断言服务器查询排空、service 关闭和单次客户端结果；它没有屏蔽线程警告。

原生 overlay 初始化的 upper.restore Result 原先被忽略，现经标准 PyResult、map_err 和 ? 传播为 Python 异常。[原生源码](../../../native/society0-filesystem/src/lib.rs) 使用常量合法 root snapshot，该调用的自然失败未在当前库前置合同中找到可达输入，报告保留这一边界。公开损坏 Bashkit snapshot 消费者验证恢复异常阻止新完整点，并可恢复旧工作区；原生重建后实际消费者和跨解释器消费者结果见 [原生/计算专项](native-compute-final.txt) 与 [跨进程专项](opaque-native-process.txt)。Python 覆盖率不计 Rust 内部路径。

## 四、交互

行动发现改用既定 FTS trigram 搜索。原 INVENTORY 命中列表的注册顺序断言改为命中集合相等，保留完整命中；[字面搜索消费者](../../../tests/primary/test_kernel_discovery_description_review.py) 仍检查空查询分页的精确总数、所有动作和注册顺序，以及短词、星号字面含义和空结果。搜索排名与完整目录遍历分别按现合同判断；信息与执行资格在注册和当前作用域重判的原负例保留。

[VFS 类型消费者](../../../tests/primary/test_core_directory_kind_review.py) 原先用目录列表第一项判断 items 目录和记录文件类型，当前目录增加元信息文件后改为按真实名称定位 items 和 1，目录/文件类型断言没有改变。SQL 原值、权限、Observation 和真实 shell/VFS 的相应消费者仍走实际产品入口。Results 页项和诊断请求字段依当前规范外壳读取，NULL、布尔、大整数、Unicode、巨正文引用、总数、原文范围及恢复值继续断言。

新增 [认证](../../../tests/primary/test_kernel_auth_acceptance.py)、[固定步与分阶段计划](../../../tests/primary/test_kernel_schedule_plans_acceptance.py) 为此前缺失的实际产品消费者。认证首轮十二项失败源于测试全局替换 httpx2.AsyncClient、连带真实 Authlib 构造未初始化；替身改为 auth 模块局部构造引用后保持真实 SDK，见 [初次工件](auth-compute-schedules-first.txt) 和 [新合同通过工件](new-contracts-second.txt)。计划消费者经 RunPlan/run_plan 检查顺序、完整发布、选点恢复和失败恢复，补测没有新增产品能力或兼容桥。

本轮迁移的主要结构性删改有当前合同与实际消费者承接，真实缺陷分别保存首次失败和修复后证据。非作者最后复审未发现冻结源码的新阻断问题；[macOS 全量](full-final.txt)、[Linux 全量](linux-linux-full-shared-final.txt)、[八组实际 wheel 消费者](clean-consumers-shared-final.json) 与最后同源共享块测量分别支持其适用范围。被拒容器、旧 fixture 和中间通过工件保留各自身份。真实服务与主体效果门，以及完整业务阶段的资源和长期增长斜率仍按对应消费者验收，正式发行身份由后续提交、包和部署共同确认。
