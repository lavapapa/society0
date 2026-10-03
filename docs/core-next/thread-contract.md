# Thread 存储合同

ThreadStore 保存主体会话、完整消息、物理请求引用、响应和工具事件。它复用同一 StageStore 的短事务和完整步骤发布；数据库恢复同时恢复 Thread 索引及正文。模型上下文由调用方明确构造，默认读取完整消息，达到模型实际限制后的处理交给 Driver。

## 一、会话与消息

插件在创建 StageStore 时将公开的 `THREAD_SCHEMA` 元组并入 schema。`ThreadStore(store)` 接受可写 StageStore 或只读 StageReader，后者供运行观察使用。权威表分为短元数据 thread_heads、追加 thread_events、独立压缩 thread_chunks 和目录计数 thread_counts。

`open(actor, moment, kind, metadata=None, provider_session_id=None)` 创建新 Thread 并返回稳定 ID。moment 接受 JSON 值或 Moment 数据类；其原文与 metadata 保存在 opened 事件中。未提供 provider_session_id 时创建一个稳定身份。`describe(thread_id)` 返回 actor、kind、provider_session_id、status、last_seq、message_count 和目录 ordinal。

`append_message(thread_id, message)` 保存完整 JSON 消息对象，返回 Thread 内单调事件序号。role、content、tool_calls 及额外提供方字段均按输入值保留。JSON 对象键须为字符串，非有限数字、循环结构及非 JSON 对象拒绝；失败回滚本次追加的全部索引和正文块。`event(thread_id, kind, payload)` 记录响应原文、错误或其他提供方事件；message、opened、closed、reopened 和 request 是专用入口保留的类型。

`close(thread_id, outcome)` 接受 completed、waiting、incomplete，追加结果事件并修改当前状态。`reopen(thread_id)` 保留原 Thread 和 provider_session_id，追加继续事件。处于 open 时重复 reopen 无新增事件。以前的激活结果继续保留，业务终止和继续激活的选择由 Driver 与调度器作出。`find(actor, moment, kind="decision")` 通过 actor、规范 moment、kind 与 ordinal 索引取得最新会话，使恢复后同一时点可继续原 Thread。incomplete 会话是否允许继续由 Driver 的预算合同判定。

## 二、请求与读取

`record_request(thread_id, provider_options=..., physical_request_id=..., message_seqs=None, retry_of=None, through=None)` 在发出物理请求前保存请求身份、完整非秘密参数和消息引用。`snapshot_messages(thread_id)` 在同一短读事务返回完整 messages 与 through 水位。提供方在首次请求时取得此快照，所有物理重试把同一个 through 传给 record_request，保证证据与实际发送 messages 一致。省略 through 时记录当下消息水位，读取时选取同 Thread 内水位之前的全部 message；后续追加消息不会进入旧请求。显式 message_seqs 可保存子集或重排，按指定顺序重建。该显式形式的引用空间随所选消息数增长。

`read_request(thread_id, seq)` 返回 messages、provider_options、physical_request_id、retry_of 和 provider_session_id，可重建原请求内容。提供方 adapter 负责在调用前统一追加 system 和新 operating_context 消息，保证所记录引用对应实际发送内容；密钥不得进入 provider_options。物理重试保存新的 physical_request_id 并关联 retry_of。响应原文作为独立 event 追加，存储失败必须向上报告，调用方不得因此重放已完成的模型请求或行动。

`read_messages(thread_id, after_seq=0, max_messages=None)` 默认返回完整消息序列；它按消息索引分批定位并重建选中消息，完整模型请求仍具有选中上下文长度的内存下界。max_messages 是显式分页工具，Driver 不得把它用作静默裁剪。

`tail(thread_id, after_seq=0, limit=100, inline_payload_bytes=65536)` 返回 items、next_seq、total。每项包含 seq、kind，小正文内联 payload；超出阈值时返回含 thread_id、seq、total_bytes 和 read_method 的 payload_ref。total 和项目在一个短 SQLite 快照内读取。末尾页保持最后已读游标，之后可用同一游标继续查询新增事件。`list_threads(actor=None, after=0, limit=100)` 按创建 ordinal 分页，返回 items、next、total，目录计数在创建 Thread 时同步更新。`read_payload(thread_id, seq, offset=0, size=65536)` 返回原始 JSON 字节范围、total_bytes 和 next_offset。它用固定原始块大小直接定位关联的压缩块；中间范围无需解压之前正文。页数及内联阈值共同约束观察响应，模型所需的完整读取保持独立明确。

`save_tool_result(thread_id, call, content, metadata=None)` 在一个事务中保存调用原文事件、tool 消息和 call_id 索引，返回消息序号。`get_tool_result(thread_id, call_id)` 通过索引还原 call、content 与 metadata，支持完整步骤恢复后的行动回执复用。相同 call_id 与相同内容返回原回执，不重复追加；不同调用或内容会拒绝。正文 content 由 tool 消息保存一份，索引引用该消息。它记录已经完成的结果，不能逆转数据库之外的行动副作用。

`register_artifact(thread_id, reference, artifact_ref, actor=...)` 将 shell 返回的 reference 关联至 run 内封存路径，验证 Thread 主体并在同一次事务调用 Writer.include_artifact。`lookup_artifact(...)` 按主体和引用取得该路径；`read_artifact(..., offset=0, size=65536)` 返回 data、total_bytes、next_offset 和 source。原文件由 StageStore.prepare_artifact 创建，恢复从完整描述符复制文件，Thread 索引随 changeset 恢复。关联后引用不可改指其他文件。

`append_input(thread_id, messages, consumer, cursor, context=None)` 在同一短事务追加整批输入消息并登记消费游标，返回最后事件序号。`input_cursor(thread_id, consumer)` 按索引读取该消费方的 JSON 游标，尚未登记时返回 None。消息或游标编码失败会共同回滚；完整步骤恢复同时还原消息与游标。游标正文复用分块事件编码，索引保存事件序号。可选 context 是本次更新的完整 system 消息，先于本批输入追加；同一事务登记其序号。省略 context 时保留旧引用，`input_context(thread_id, consumer)` 读取该引用对应的原消息，尚未登记时返回 None，避免在游标里重复保存材料正文。

## 三、成本与恢复

写入端递归产生 JSON 字节片段，巨大字符串按 8192 字符转义，再组装最多 65536 原始字节的块，每块独立 zlib level 3 压缩后进入 BLOB 行。Session 捕获、changeset 和 root 因此保存压缩块；热元数据更新不携带巨大正文。该实现采用单线程压缩，多核有界流水线留待端到端数据支持后接入。

消息与事件同一次 StageStore transaction 提交，失败不会留下半条事件。完整步骤发布自然包含其变更；未完成步骤里已经短事务提交的 Thread 可由 StageReader 读取用于诊断，restore 仅重建所选完整步骤。ThreadStore 没有独立的 checkpoint 或 marker，也没有第二套回滚日志。

活动 append 和 tail 从 thread_heads、主键及消息索引取得当前投影，成本由本次正文大小和请求页决定。默认 request 水位记录是固定数量字段，避免逐轮复制完整 messages；显式重排请求仍按真实所选序列保存引用。完整读取历史与请求重建的工作量随所读内容增长，该成本服务于保留全部上下文的语义。

本机小型试验及环境信息保存在 `research/core-next/thread-size-results-range-20261004.json`，原始测试输出保存在同目录 kernel-threads 文件。高重复与异质正文均逐值恢复相等；RSS 记录是进程历史高水位及其差值，输入生成本身的峰值可能遮住写入瞬时增量。磁盘数据包含独立 current、初始 root、changeset 和恢复后新 root，不能把压缩块大小视为整个运行目录大小。后续真实模型和记忆集成仍需独立验收。

## 四、物理调用

`record_provider_request(thread_id, **options)` 接收与 `record_request` 相同的请求参数，另外在本事务维护真实物理调用投影。每次物理尝试使用唯一 physical_request_id；provider_options.model 保存实际发送模型。`record_provider_event(thread_id, kind, payload)` 接收 provider_response、provider_error、provider_decode_error 或 provider_cancelled，payload 包含 physical_request_id 和 payload 正文。正常响应原始 SDK 数据位于内部 payload.raw_response，解码错误的原始数据位于内部 payload.response；token 用量读取原始数据的 usage。一般事件使用 `event`，不根据事件名称推断物理收费。

例如，自定义提供方先调用 `record_provider_request(tid, provider_options={"model": "chosen-model"}, physical_request_id=request_id, through=watermark)`，发送对应完整快照后调用 `record_provider_event(tid, "provider_response", {"physical_request_id": request_id, "payload": {"raw_response": full_response}})`。留证失败向调用方传播；已经发送的物理请求不因本地统计或正文写入失败而重发。累计查询和主体共享批次归属见 `models-contract.md` 的用量部分。

LLM 实际行动通过 start_action/finish_action 保存开始与结束事件及短投影，关闭时通过 close 的 reason、elapsed_s、phase_timings 参数保存终止事实。普通记录入口保持独立。调用归属、回执复用和时长范围见 [行动与时长诊断](diagnostics-contract.md)。
