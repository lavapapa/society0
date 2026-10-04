# 新版 Core 能力对照

本文是本次重构的能力盘点与语义验收索引。新版面向一个共享 Environment，内部机制由分层依赖插件提供；旧函数名、类继承、状态代理、文件格式和模块布局可以改变。表内“保留”表示需要取得等价行为的证据，“重设”表示按当前用户要求确定新合同，“新增”表示旧代码尚不能作为验收证据。本文的代码与测试引用说明已有资产及风险覆盖，本轮盘点没有把测试存在等同于新版已经实现。

## 一、基线

对照基线包括实时观察分支的有效引擎能力和最新 main 的独立研究者入口。两条分支的差异按共同祖先核对，避免将已经删除的旧执行器重新引入新版。

### 1.1 身份与证据

引擎基线为 `96b1f3b11aee3c146f5b43e0b8158ec29294e98a`，main 输入为 `7031b06`，共同祖先为 `7e98f11dcc4d5e0f5ace6ed73edca509d7e8e17a`。当前重构工作树的合并基线为 `738ba70cf7881649c38422d4b3f00ffed12ab51c`，分支为 `codex/society0-core-next`。这些身份属于本次迁移记录，后续实现不能以覆盖本表中的旧基线来替代逐项验收。

main 相对共同祖先没有 `src/`、`pyproject.toml` 或 `uv.lock` 的独立产品变更；其有效新增集中在 skill、两轮完整实验示例、工作台、研究者引导与对应测试。直接比较 `96b1f3b..7031b06` 会看到大量旧引擎代码，这源于实时观察分支尚未合入 main，不能据此把 SimEngine、YAML StepFlow、socket streaming 和重复事件分发器列为必保能力。

代码引用均相对仓库根目录，测试写法为 `文件::符号`。同文件中的参数化用例、相关负例及直接消费者需要一起迁移；代表测试不构成删减其余测试的许可。完整文件索引见第六节。新测试编号和证据位置由 [TODO](TODO.md) 跟踪。

### 1.2 迁移判断

首要保留的是主体能获得的信息、可执行的行为、行为顺序、记忆结果、失败范围及恢复后事实。动态发现和分页可以改变交互方式，完整内容必须仍可取得；LLM 工具路径变化还需要比较主体的查询、决策与任务结果。基线不存在的 VFS、对象相关 action discovery、插件依赖生命周期和可替换 Driver/Schedule 属于新增能力，不能借旧版测试数量宣布完成。

当前用户要求完整 Thread 输入，因此 `agent/agent_loop.py::_request_messages` 的 `max_request_messages` 请求投影裁剪列为重设项。原 `test_action_loop_bounds_request_history_without_truncating_thread` 证明的是旧行为，不能继续作为新版成功标准。基线 Thread 校验链、`error_fingerprint`、checkpoint hash 等属于旧实现；新版遵循不新增哈希、checksum、fingerprint 的约束。保持错误分类、事实关联和完整发布能力，无需保留上述字段算法。

基线具有 transparent_proxy 与 explicit_transactions 两条写入路径。新版可统一其物理接口；应迁移读己之写、原子业务修改、冲突与失败处理、追加事实不可改、值类型及顺序等语义。旧 raw alias、任意 Python dict 别名、旧 checkpoint 解码兼容不属于交付要求。

## 二、主体

主体侧验收分为 Driver、行动交互、Thread 与记忆。LLM Driver 必须保留完整推理与行动能力，规则 Driver 无需经过文本或 shell 适配。

### 2.1 身份、驱动与认知输入

| ID | 能力与新版归属 | 基线代码证据 | 代表测试与语义验收 |
|---|---|---|---|
| A01 | 保留：Actor 身份、类型、persona、state、properties，临时 reminders；归 Actor/Driver | `core_data.py::World.get_agent/add_agent_data/remove_agent`；`agent/core.py::Agent` | `test_agent_persistence_semantics_v4.py::test_agent_properties_restore_and_reminders_reset_to_default`；身份与持久字段恢复，临时提醒依合同重置 |
| A02 | 保留：LLM 与 rule 两种主体；可替换 Driver 新增 | `agent/core.py::LLMAgent/RuleAgent`；`World.initialize_all_cognitive_systems` | `test_society0_primary.py::test_world_strict_cognitive_initialization_requires_memory`；rule 路径不调用模型，LLM 使用真实同一调用链 |
| A03 | 保留：persona、环境说明、当前状态、FoV、提醒与记忆构成认知输入；改由 View/Driver 组装 | `LLMAgent._build_system_prompt/get_llm_visible_state`；`World._collect_fov_results` | `test_society0_primary.py::test_world_instruct_logs_fov_preview_without_full_result_by_default`；查看日志缩略与实际模型完整输入分别验收 |
| A04 | 保留：同一 Thread 连续激活、历史工具结果、provider session 身份 | `LLMAgent.instruct`；`execute_action_loop(prior_messages)` | `test_action_loop_can_continue_an_existing_agent_thread`、`test_agent_thread_reuses_provider_session_id_across_tool_turns`；另见 `test_thread_integrity.py::test_same_tick_second_provider_request_replays_action_result_on_same_thread` |
| A05 | 重设：完整模型输入与同 moment 信息会话；无固定历史窗口 | `execute_action_loop::_request_messages` 为旧裁剪实现 | 用新负例证明长 Thread 全历史仍输入；同 moment 新消息增量呈现且旧内容继续可见；物理上下文上限为 incomplete，禁止假定成功 |
| A06 | 保留：instruct 行为路径与 interview 测量路径分离 | `LLMAgent.instruct/interview`；`AgentGroup.instruct/interview` | `test_structured_interview_exposes_submit_result_only`、`test_world_interview_defaults_to_submit_result_loop_not_direct_json`；访谈默认无普通领域 action，不自动保存访谈经验 |
| A07 | 保留：单次模型选择覆盖主体/类型/默认配置，显式认知阶段与参数覆盖 | `World._resolve_model_selection`；`normalize_reasoning_stages`；`ModelProvider` | `test_code_step_llm_selects_registered_model`、`test_llm_model_request_options_are_defaults_for_every_call`；阶段/输出/预算作为运行合同传递 |
| A08 | 保留：批量选择、顺序、采样、predicate 与结构化结果读取 | `AgentSelector`；`AgentBatchResult` | `test_agent_group_selection`、`test_agent_batch_result_exposes_action_summaries`；选择结果身份与顺序、values/mean/table、逐主体错误可追溯 |

表中未标文件名前缀的 `test_*` 均位于 `tests/primary/test_society0_primary.py`。

### 2.2 LLM 行动循环

| ID | 能力与新版归属 | 基线代码证据 | 代表测试与语义验收 |
|---|---|---|---|
| L01 | 保留：动作标签、精确名称、空工具集、角色限制；动态 meta tools 新增 | `World.assemble_agent_actionset/_validate_action_filter`；`ActionSet` | `test_exact_action_filter_does_not_expand_same_named_tags`、`test_empty_action_tags_filter_exposes_no_actions`；`test_action_role_permissions.py` 全组；发现及执行均遵守 actor 合同 |
| L02 | 保留：参数 schema、opt-in strict、nullable/default/enum/nested array 处理 | `function_registry.py::validate_strict_function_parameters`；`ActionSet.call_action` | `test_strict_normalization_keeps_optional_enum_nullable`、`test_provider_replay_rejects_invalid_arguments`；`test_external_environment_injection.py` strict 全组；非法参数不得执行领域动作 |
| L03 | 保留：terminal 成功才结束、completion tag、required action/tag 的循环内纠正 | `execute_action_loop` requirement/completion 检查 | `test_terminal_action_requires_success_before_ending_loop`、`test_required_action_gets_correction_turn_when_model_stops_early`、`test_completion_action_tag_ignores_semantic_action_failure`；显式研究约束才启用 required，不全局强迫行动 |
| L04 | 保留：自然完成和无行动；受理、业务成功、激活完成分别表达 | `LoopResult`；`_semantic_action_status` | `test_action_loop_without_turn_limit_ends_on_natural_model_completion`、`test_semantic_action_status_does_not_treat_failure_rate_as_action_error`；新版结构化 outcome 可替换旧文本错误猜测 |
| L05 | 保留：max_turns、总 action、逐 action 限额与失败尝试计数 | `execute_action_loop` budget preflight | `test_failed_action_attempts_consume_the_action_budget`、`test_oversized_tool_call_batch_is_rejected_before_any_action_executes`、`test_action_budget_exhaustion_does_not_issue_a_tool_free_closing_request`；预算触达为 incomplete，不额外收尾或重跑已成功行动 |
| L06 | 保留：重复 call ID/重复工具抑制、批量预检与原有逐次行为顺序 | `execute_action_loop` action batch preflight | `test_duplicate_tool_calls_are_suppressed_before_budget_accounting`、`test_tool_call_batch_exceeding_one_action_limit_rejects_every_call`、`test_invalid_sibling_does_not_rollback_successful_action`；正常独立 action 不误作整批原子业务 |
| L07 | 保留：parallel_tool_calls=False 的提供方与运行时合同 | `execute_action_loop::_parallel_tool_call_batch_error` | `test_parallel_false_rejects_multi_action_turn_then_executes_one_corrected_action`、`test_parallel_false_second_multi_action_turn_is_incomplete_contract_error`、`test_parallel_false_invalid_multi_action_batch_ends_without_retry` |
| L08 | 保留：输出 length 截断优先判 incomplete，完整-looking tool 也不执行 | `execute_action_loop` finish_reason 分支 | `test_output_token_limit_with_complete_tool_call_does_not_execute_terminal_action`、`test_length_takes_precedence_over_multi_action_and_discards_truncated_text`；`test_real_action_contract.py` budget/length 两负例禁止成功记忆 |
| L09 | 保留：空响应、传输重试、上下文上限与 action schema error 的不同失败范围 | `execute_action_loop` provider/empty retry；`recovery.py::classify_step_failure` | `test_provider_transport_retry_repeats_only_the_current_model_request`、`test_tool_schema_error_returns_to_same_activation_without_replaying_successful_actions`、`test_provider_context_limit_keeps_the_tick_running_with_an_incomplete_activation`；配置化重试与温度变更留证 |
| L10 | 保留：重复读取的反馈、跨工具事实覆盖及写后失效；不以重复读强迫结束 | `_read_outcome_signature` 与 fact-key/decision-receipt 处理 | `test_cross_tool_read_fact_union_is_presented_and_merged`、`test_changed_write_clears_cross_tool_fact_and_read_caches`、`test_noop_write_keeps_cross_tool_fact_coverage`、`test_repeated_read_with_same_result_continues_until_max_turns`；新版元工具必须关联实际领域 action 与返回事实 |
| L11 | 保留：结构化输出与 submit_result，一次成功终止；直接 JSON 为无动作测量 opt-in | `LLMAgent.instruct` structured output；`StepContext.llm_json` | `test_submit_result_terminates_structured_instruct_without_extra_llm_turn`、`test_structured_interview_direct_json_fast_path_uses_one_llm_call_when_enabled`、`test_structured_output_strict_request_does_not_silently_downgrade`；JSON repair/fallback 不静默放宽 schema |
| L12 | 保留：task-local call ID、可见文本/tool 顺序、失败与只读 action 记录 | `current_action_call_id`；`ExecutionContext`；loop trace | `test_concurrent_action_call_ids_do_not_leak_between_tasks`、`test_assistant_turn_trace_preserves_visible_text_and_tool_order_only`、`test_action_context_provider_records_action_failures`；已成功业务不能因后续日志错误改写成“未执行” |

### 2.3 Thread 与记忆

| ID | 能力与新版归属 | 基线代码证据 | 代表测试与语义验收 |
|---|---|---|---|
| M01 | 保留：完整请求/响应/工具/运行消息/物理重试记录；大正文外置引用 | `agent/thread_store.py::AgentThreadStore`；`LLMManager._append_agent_thread_event` | `test_agent_thread_store.py::test_llm_manager_records_every_physical_retry_attempt`、`test_read_messages_replays_latest_request_response_and_runtime_messages`；调用原文可完整读取，摘要不能替代原文 |
| M02 | 保留：Thread open/append/close、身份、partial tail、closed prefix 与完整 checkpoint 关联 | `AgentThreadStore.open_thread/close_thread/publish_epoch_manifest` | `test_thread_integrity.py`；`test_agent_thread_store.py::test_v4_epoch_manifest_collects_closed_threads_across_ticks_atomically`；失败开放 Thread 留作诊断，不假装完整恢复点 |
| M03 | 保留召回/提取，补齐：主动 memory tools 独立配置 | `LLMAgent.instruct/extract_memories_from_thread`；`Memory`（未发现默认已注册的主动记忆工具） | `test_llm_agent_instruct_uses_configured_memory_top_k`；`test_thread_native_memory.py`；新增 2³ 配置组合及访谈负例，关闭召回不关闭写入、不移除主动工具 |
| M04 | 保留：原 Thread 内提取，失败继续同 Thread，空选择成功，禁止失败后兜底造记忆 | `agent/memory_extraction.py`；`LLMAgent.extract_memories_from_thread` | `test_thread_native_memory.py::test_memory_extraction_retry_continues_the_same_thread`、`test_failed_thread_memory_extraction_never_writes_fallback`、`test_empty_memory_selection_is_success_and_writes_nothing` |
| M05 | 保留：pending→向量写→receipt、稳定幂等身份、同 key single-flight | `LLMAgent._thread_memory_commit_state`；`Memory.inspect_memory_ids` | `test_memory_commit_receipt_failure_recovers_without_second_llm_or_memory`、`test_concurrent_memory_extraction_same_key_is_single_flight`；`test_memory_receipt_recovery.py`；不能从本地记录推导任意外部 exactly-once |
| M06 | 保留：episodic/semantic、逐条内容/向量/重要性/时间、增删改与导入导出 | `MemoryEntry`；`Memory.add_memories_batch/update_memory/delete_memory/export_memories/import_memories` | `test_memory_visibility_v4.py::test_update_and_delete_close_visible_interval_without_rewriting_old_tick`；需补无损值与类型 roundtrip；基线 `_evaluate_importance` 固定 3.0，不冒称已实现 LLM 重要性评分 |
| M07 | 保留：向量距离、importance/时间衰减、去重排序、top_k 与空集合免请求 | `Memory.retrieve/_query_collection_with_retry` | `test_memory_retrieve_skips_embedding_when_current_agent_has_no_memories`、`test_memory_visibility_v4.py`；新索引需对照候选及最终模型上下文，不能以少召回换性能 |
| M08 | 保留：branch/tick/epoch 可见性、当前 active write、fork 不污染祖先 | `memory_view.py::PublishedEpochs`；`Memory.set_memory_view/set_write_epoch` | `test_memory_visibility_v4.py`、`test_memory_epoch_sharing.py`；未提交 future 不可恢复，同激活所需新记忆不得被滞后索引漏掉 |
| M09 | 保留：并发提取、逐主体嵌入合批、一一对应、物理请求归属 | `AgentGroup.extract_thread_memories`；`EmbeddingManager` microbatch | `test_embedding_microbatch_coalesces_distinct_agent_threads`、`test_embedding_microbatch_preserves_plural_trace_metadata`；`test_embedding_thread_trace.py`；不可合并不同主体文本或重复记同一物理调用 |
| M10 | 保留：Chroma 持久资源关闭、跨运行 seed、close 失败可诊断 | `PersistenceManager` Chroma lifecycle | `test_persistence_lazy_chroma.py`；`test_real_process_recovery.py::test_real_exit_restore_memory_and_observation`；向量后端替换需额外召回等价证据 |

## 三、环境

新版插件的组合发生在共享环境内部。现有领域能力、扩展方式和调度行为提供对照样本；不同插件的数据、业务依赖和物理存储边界可以分别选择。

### 3.1 环境、调度与并发

| ID | 能力与新版归属 | 基线代码证据 | 代表测试与语义验收 |
|---|---|---|---|
| E01 | 保留：环境声明、config/state/agent-managed schema，能力发现及外部注入 | `decorators.py::env_type/capability`；`EnvRegistry`；`Environment`；`Society0(environment_factory)` | `test_external_environment_injection.py`、`test_persistence_architecture_v4.py`；新 plugin 可复用元数据语义，保持领域无关且无需全局注册污染 |
| E02 | 保留：FoV/action/rule/behavior 的不同用途、参数绑定、能力目录与友好错误 | `CapabilityCatalog`；`FunctionRegistry`；`StepContext.rule/behavior` | `test_capability_catalog_and_missing_logic_errors`、`test_code_step_experiment_env_action_is_discoverable_and_agent_callable`；不用名称猜测错误能力种类 |
| E03 | 保留：sync/async initialize/before/after、失败时 after 不冒充成功、step runtime 共享失效 | `EnvironmentTickContext`；`CodeSchedule.execute_tick`；`StepRuntimeScope` | `test_env_tick_hooks.py`、`test_step_runtime_scope.py`；临时 namespace 不持久化，缓存不是权威事实或一致快照 |
| E04 | 保留：自定义环境快照中的 graph/数值派生结构与资源注入 | `Environment.snapshot/restore_from_snapshot/set_resource_handles`；social graph restore | `test_external_environment_injection.py` 与 builtins restore；新增非 JSON 自定义扩展测试，whole-component 成本声明，不承诺任意 opaque 分页 |
| S01 | 保留：代码 step 注册、按序执行、完整步骤边界、返回 metrics/tables/state | `Society0.step/add_step/run`；`CodeSchedule`；`StepResult` | `test_code_schedule_smoke_outputs_and_checkpoints`、`test_env_tick_hooks_order`；新 Schedule 插件明确 moment、phase 和中途失败 |
| S02 | 保留：直接 rule/behavior、独立模型测量与结构化 JSON | `StepContext.rule/behavior/llm/llm_json` | `test_code_step_rule_and_behavior_helpers`、`test_code_step_can_call_llm_with_structured_json_output`、`test_code_step_llm_reports_schema_validation_error` |
| S03 | 保留：有界 Agent 批处理及并发优先级、逐主体失败隔离 | `_run_limited`；`_resolve_agent_call_concurrency_info` | `test_explicit_concurrency_overrides_world_default`、`test_agent_group_keeps_one_provider_activation_error_local_to_the_subject`；显式 group→运行→model→默认，有效来源进入输出 |
| S04 | 保留：动态 activation pool、立即补位、去重合并、同主体串行键 | `activation_pool.py::ActivationPool` | `test_activation_pool.py` 全组；活跃重复提交合并为后续激活，不同时重入同主体，不用等待串行键占满有效容量 |
| S05 | 保留：drain/close 边界新增任务、取消、失败、剩余 activation budget | `ActivationPoolSession` | `test_drain_waits_for_work_submitted_on_idle_boundary`、`test_activation_pool_limit_surfaces_unfinished_follow_up_without_running_it`；关闭/取消不遗留后台领域写入 |
| S06 | 新增：可替换 step/阶段 Scheduler 与 Driver yield/resume | 现有 `CodeSchedule`/`ActivationPool` 提供部分行为基线 | 新验收：同 moment 重激活、前序变化可见、固定阶段快照可选、await 与模拟时间区分、待完成行动恢复；原代码不提供通用事件调度承诺 |

### 3.2 内置场景与信息交互

| ID | 能力与新版归属 | 基线代码证据 | 代表测试与语义验收 |
|---|---|---|---|
| B01 | 保留：plain 小环境，低依赖 rule 路径 | `env/plain/env.py::PlainEnvironment` | `test_plain_environment_does_not_import_networkx`、`test_plain_environment_runs_with_explicit_read_view`；无需求时不初始化向量/图/模型资源 |
| B02 | 保留：round_robin 配对计划、轮次、参与者、伙伴消息、组广播、消息保留及 FoV | `RoundRobinConversationEnv`：`advance_round_robin_with_pairing/send_message_to_partner/broadcast_to_group/get_conversation_fov/get_group_fov` | `test_e2e_builtin_round_robin_rule_behavior_and_capabilities`、`test_builtin_persistence_semantics_v4.py`；逐条消息/双方可见内容/轮次/恢复对照；pairing_strategy 在环境文件中只有 schema 与构造赋值，多个策略的实际效果须补规格与测试，不按配置枚举冒称齐备 |
| B03 | 保留：social 拓扑、发帖/点赞/评论/转发/关注/取关、通知、资料/详情、规则干预 | `env/social_network/env.py` public capabilities；`models.py` network variants | `test_social_network_recommendation.py`、真实 social 测试；NetworkX 图及关系恢复、原文/顺序、通知和受影响对象对照 |
| B04 | 保留：social 推荐、语义相似、热度/时间/参与度、活跃池、曝光与 after_tick 批处理 | `_rank_posts_with_similarity/_score_posts/_get_recommendation_cache/_flush_pending_post_embeddings` | `test_recommendation_scores_full_active_pool`、`test_semantic_query_uses_active_pool_not_recent_sample`、`test_recommended_feed_preview_has_no_impression_or_state_side_effect`、`test_publish_post_embeddings_flush_in_one_batch_after_tick`；保推荐研究语义，允许取得完整 post 详情 |
| B05 | 重设：social 展示截短与通用完整信息边界 | `_render_recommended_feed`；`get_post_details` | `test_recommended_feed_truncates_long_content_and_total_prompt` 是领域展示行为，不能推广到 Thread 裁剪；新 View 必须区分预览与完整原文并证明主体可继续读取 |
| N01 | 新增：ResourceRef/View、对象类型与实例、动态 action templates+targets | `CapabilityCatalog` 和角色限制为局部资产 | 对象删除/变化后重新判执行条件；不预枚举目标组合；发现过程尊重信息范围；受理不等同成功 |
| N02 | 新增：Relation/Access 的 discover/read/invoke 与 actor runtime 绑定 | 现有 `role/roles` 只覆盖类型限制 | 社会权利与实际可执行分别表达；领域关系使用插件权威事实，避免维护第二份全局万能关系图；规则与 LLM 使用相同权限语义 |
| N03 | 新增：push 与主动读共享 View、document/dataset 完整入口、分页/query/tail/sample | 现有 FoV 和 observation 为不同侧资产 | 同视图来源/水位与全文可得；抽样方法/总量透明；调查 action 可消耗时间/预算，已提供信息读取依模型合同 |
| N04 | 新增：lazy VFS、私有 workspace、自主 shell/数据分析适配 | 基线无 Agent VFS/shell 工作区 | actor workspace 可读写查删；写分析文件与改变世界分开；世界写经 action 合同；不解析任意 shell 为 SQL，下推走明确数据查询接口 |
| N05 | 新增：分层插件依赖、实例绑定、运行冻结、资源生命周期 | `EnvRegistry`/资源注入/runtime namespace 可复用 | 同一共享 Environment，多实例显式依赖；缺 required 启动报错，optional 明确语义；有限资源按需获取；普通 rule driver 不强制 AgentOS |

## 四、运行

资源服务、状态与观察共同决定长期实验是否可信。这里保留现有有效恢复及可观测行为，同时记录需要改善的成本边界。

### 4.1 Provider 与运行输出

| ID | 能力与新版归属 | 基线代码证据 | 代表测试与语义验收 |
|---|---|---|---|
| R01 | 保留：OpenAI/兼容端点、Azure、Ollama 声明，多模型/endpoint、请求默认值 | `models.py::LLMModel/EmbedModel`；`llm_model_types.py::ModelProvider` | `test_model_declaration_builds_endpoint_configs`、`test_managers_route_mixed_trust_env_endpoints_to_distinct_pools`；能力按适配器分别验收，不从兼容名推断所有提供方已真测 |
| R02 | 保留：并发容量、排队、timeout、端点选择、物理重试和关闭 | `LLMManager/EmbeddingManager` | `test_llm_manager_enforces_hard_timeout_and_logs_failure`、`test_openai_azure_and_embedding_clients_disable_sdk_retries`；失败/取消释放槽位，重试不重复领域动作 |
| R03 | 保留：native/auto_restrict tool choice、strict/parallel/额外请求选项 | `LLMManager._resolve_tool_choice`；`_merge_llm_request_options` | `test_tool_choice_policy.py`、`test_llm_manager_http_transport_preserves_tool_contract_flags`；Qwen 已验工具阶段合同不能外推到所有推理场景 |
| R04 | 保留：embedding model/dimension 校验、send_dimensions、有限 cache、合批/分批与关闭清空 | `EmbeddingManager.request/_execute_microbatch_with_split`；`EmbedModel` | `test_embedding_thread_trace.py`、microbatch 相关 primary tests；各输入逐条返回，model/dim/text 边界不串数据 |
| R05 | 保留：trust_env/proxy、provider session、请求 trace、凭据脱敏 | `resource_managers.py`；`LLMAgent::_with_thread_session_id` | `test_ollama_embedding_client_can_inherit_proxy_when_requested`、`test_thread_integrity.py::test_request_credentials_are_redacted_recursively`；本轮盘点不读取任何密钥 |
| O01 | 保留：steps/metrics/events/summary/resources/diagnostics 的实际数据输出 | `Society0._save_summary`；`EventLogger`；`diagnostics.py` | `test_e2e_default_run_writes_expected_artifacts_and_state`、`test_runtime_diagnostics.py`；不把未完成或无数据运行展示为成功 |
| O02 | 保留：逐 Agent/工具成功失败、tags、termination、时长、并发来源及阶段计时 | `AgentBatchResult`；`_record_agent_batch_event`；resource summary | `test_society0_summary_counts_nested_agent_actions_without_double_counting`、`test_society0_timing_breakdown_can_identify_jitter_as_bottleneck`；统计不重复物理调用，不复制大载荷 |
| O03 | 保留：大表/生成器流式数据集、单巨行完整读取、准确 checkpoint 登记 | `result_datasets.py`；`StepResult.to_dict` | `test_result_datasets.py`、`test_summary_storage_independent.py`；失败数据留诊断，summary 多分区同容器身份正确、明细完整 |
| O04 | 保留：运行轻状态、进度/heartbeat、诊断写失败不改变业务事实 | `Society0._write_runtime_status`；batch heartbeat | `test_review_runtime_status_write_failure_does_not_change_business_run`、`test_agent_group_instruct_writes_heartbeat_events_while_in_flight`；区分可丢诊断和权威事实写失败 |

### 4.2 状态、恢复与观察

| ID | 能力与新版归属 | 基线代码证据 | 代表测试与语义验收 |
|---|---|---|---|
| P01 | 保留：replaceable entry、append-only map/list、transient、nested declaration | `state_persistence.py`；`PersistenceSchema`；`StateDeltaJournal` | `test_persistence_declarations_v4.py`、`test_state_persistence_api_v4.py`；未知声明/重复事实提前拒绝，保 typed key、值精度、插入及列表顺序 |
| P02 | 保留语义/重设接口：透明写、显式原子组、局部校验、冲突、租期失效 | `state_proxy.py`；`state_transactions.py` | `test_explicit_state_transactions.py`、`test_explicit_state_transaction_concurrency.py`、`test_state_proxy_collections.py`；跨 await/alias 新合同显式测试，不为原 dict 别名复制整个 World |
| P03 | 保留：根发布、封存 delta、多步 epoch、失败丢弃、完整标记唯一恢复边界 | `Society0.run`；`PersistenceManager.publish_root/publish_delta/discard_unpublished_epoch` | `test_runtime_checkpoint_v4.py`、`test_incremental_checkpoint_v4_process_crash.py`、state machine；若重定义 checkpoint_every，PRD 明确新提交/备份关系且实际旧版本可恢复 |
| P04 | 保留：World/Thread/Memory 同步恢复、诊断与完整分离、身份 resolve 免物化 | `PersistenceManager.resolve_checkpoint/load_checkpoint_from` | `test_checkpoint_memory_pairing.py`、`test_checkpoint_records.py::test_identity_resolution_never_restores_world_and_keeps_corruption_fallback`；任何发布描述必须对应真实可恢复数据 |
| P05 | 保留：跨目录 fork、不可变来源引用、初始化差异、分支隔离与可达清理 | `set_checkpoint_base`；`V4CheckpointStore` branch/GC | `test_incremental_checkpoint_v4_branch_gc.py`、`test_base_checkpoint_root_reuses_history_and_applies_initialization_changes`；来源依赖与缺失明确，fork 不复制全历史 |
| P06 | 保留：analysis/restore 自包含 bundle 及 Memory 退出条件 | `V4CheckpointStore.export_bundle` | `test_bundle_rewrites_dependency_and_restores_without_sources`、`test_restore_bundle_requires_offline_memory_writer`；分析导出不能假称完整恢复包 |
| P07 | 保留：流式 root/巨记录、可定位正文、范围读、顺序 metadata、有界读写 | `checkpoint_records.py::write_records/read_page/iter_metadata/RecordReader` | `test_storage_v3_independent.py`、`test_checkpoint_records.py`、`test_storage_review.py`；旧 codec 可替换，不能回退到每次整 World JSON/整记录解压 |
| P08 | 保留：失败分类、失败步骤不可续跑、可信完整点新实例恢复 | `recovery.py`；`Society0.run/restore` | `test_step_recovery.py`；业务拒绝与一致性丢失分别处理，partial write 不能被普通 tool error 吞掉后继续发布 |
| Q01 | 保留：在线/离线同一只读服务、独立进程、status 与 committed/indexed 水位 | `observation.py::ObservationReader`、CLI/HTTP | `test_observation.py::test_independent_query_while_producer_blocks`、`test_http_status_remains_available_during_indexing`；producer 阻塞和冷索引追赶时状态可读 |
| Q02 | 保留：固定版本 state page、typed cursor、精确总数/顺序/字节预算、巨内容范围读 | `state_page/read_content` | `test_observation_review.py`、`test_observation_compact.py`；换 run、fork source、父删除重建、同 epoch 多次写与 reader 重启均正确 |
| Q03 | 保留：Thread live 与 committed prefix、payload refs、dataset 分区/提交边界 | `agent/thread_observation.py`；`thread_page/dataset_page` | `test_thread_observation.py`、`test_summary_storage_independent.py`、`test_review_dataset_reference_metadata_respects_page_byte_budget` |
| Q04 | 保留：本地派生索引、损坏/中断重建、显式 prepare、大历史不进入普通热页 | `sync/prepare_state/prepared_state/clear_prepared_state` | `test_observation_compact.py`、`test_query_storage_independent.py`；后台追赶/固定页短事务/单准备槽，索引文件及 WAL 峰值需计 |
| Q05 | 保留：HTTP 有界接纳、慢请求/慢响应不阻塞其他读者 | `observation.py` server handler | `test_observation_http_independent.py`、`test_http_slow_response_reader_does_not_block_status`；饱和明确 busy，断开释放槽，readonly handler 不创建/修改权威数据 |
| Q06 | 新增：持续 Thread tail/目录与 sealed 完成步 observed revision | 基线 `watch` 最多返回一条状态快照；Thread cursor 固定 boundary | 基线没有 Thread append 绑定 watch，也没有完整 tail API；新验收需覆盖末页后新增、慢消费者、固定 view 过期、active/observed/committed 分离 |

## 五、迁移

研究者入口也是可用产品的一部分。它们采用新 API 后仍要让研究者独立完成小实验、看到真实产物并识别模型和软件各自的限制。

### 5.1 main 独立能力

| ID | 输入与有效能力 | 基线证据 | 新版验收 |
|---|---|---|---|
| U01 | 研究者分阶段引导、可见实验 todo、领域研究参考、运行与分析指引 | `skill/SKILL.md`；`skill/references/researcher-onboarding.md`、`study-patterns.md`、`workbench-guide.md` | 新插件入口下仍能理解研究问题、冻结配置、运行完整 pilot、检查原始证据；不把 preflight 当真实实验 |
| U02 | 两轮曝光→显式保存记忆→访谈测量的完整入门实验 | `skill/assets/minimal_experiment.py`；`tests/primary/test_skill_starter.py` | preflight 不调用提供方；当前信息与 recalled memory 分离；每阶段失败可见；新 engine 不继承前次可变配置 |
| U03 | 工作台运行/tick/主体精确资料、配置编辑差异、虚拟列表与机制/会话视图 | `tools/workbench-template/src/model.js`、`views.jsx`、`ConfigPanel.jsx`；`tests/model.test.mjs`、`render.test.mjs` | 零伪造主体/记录，删增主体按 ID，指标与可用资料匹配；配置改动生成新版本请求，不能静默改运行历史 |
| U04 | 单文件工作台渲染及输入数据范围 | `skill/scripts/render_workbench.py`；`test_workbench_renderer.py` | run/tick/snapshot 范围完整、版本 ID 唯一、数据真实嵌入；按新产物适配，不修改既有报告真实性 |
| U05 | 安装/公开说明、WorkBuddy 使用案例与平台中立 | `README.md`；`docs/workbuddy-walkthrough.md`；`docs/validation/workbuddy-2026-10-03.md` | 安装、运行、读取、恢复实例与发行代码一致；案例证据可保历史身份，不能冒充新版已验证 |

### 5.2 尚需明确的合同

`agent/behavior_action.py::create_behavior_action` 当前返回写死的示例 behavior 名单且带未接 registry 的 TODO，属于占位代码，不能当成动态能力发现资产迁移。`Memory._evaluate_importance` 也未执行其注释中的 LLM 评分。

旧 `FunctionRegistry` 中 selector/operator/converter/empower 的注册入口有历史结构，主路径 CodeSchedule 已不依赖旧 YAML 执行器。保留用户自定义选择、规则和转换所需表达能力，待具体消费者测试确定新位置；没有消费者的旧 registry 形状不直接升格 Core 插件类别。RoundRobin schema 中声明的 pairing_strategy 同样需要逐个核对实际实现，文档枚举不自动等于已完备能力。

记忆验收必须覆盖三个独立开关：自动召回、经验提取写入、主动记忆行动。基线显式 `extract_thread_memories` 已将前两者分开，未发现默认已注册的主动记忆工具；该入口按新增能力实现，不能声称旧版已齐备。新配置需提供明确独立组合。每种组合均断言实际 retrieve、extract、embed、write 次数及内容，访谈和 incomplete 激活另设负例。

性能目标不能由小用例替代。既有实际根 JSON 约 1.879 GB、原 World 多 GiB 驻留、205k 级 delta 及单大 entry 应继续成为对照负载。基线已有流式编码、紧凑 metadata 与只读索引，却仍可能全量构造 World、整条 replaceable entry 封存、沿历史链恢复；这些是新版待改善成本，不属于必须保留的架构。所有测量区分编码副本、原生对象驻留、观察索引、WAL、外部模型等待与业务计算。

完成标准是每个保留 ID 指向新版测试/实验，重设 ID 指向获准合同及对应负例，新增 ID 指向端到端消费者。测试删改应保留语义理由；旧 API 不兼容不构成删掉业务能力的理由。

## 六、索引

以下目录索引通过合并基线的已跟踪测试文件静态解析取得，计数为 `test_*` 函数定义数，不是参数化后的执行数量，也不表示本轮已通过。完整函数名可在所列文件直接定位；第二至四节提供重点断言的锚点。新增 `tests/experiments` 归本次试验，不混入旧版基线。

| 基线测试文件 | 函数数 | 覆盖能力组 |
|---|---:|---|
| [tests/e2e/test_real_process_recovery.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/e2e/test_real_process_recovery.py) | 1 | L/M/R/P/Q；真实环境合同，默认不执行 |
| [tests/e2e/test_society0_e2e.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/e2e/test_society0_e2e.py) | 12 | A/E/B/S/O/P；跨模块完整路径 |
| [tests/e2e/test_society0_real_e2e.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/e2e/test_society0_real_e2e.py) | 13 | L/M/R/P/Q；真实环境合同，默认不执行 |
| [tests/performance/test_incremental_checkpoint_v4_performance.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/performance/test_incremental_checkpoint_v4_performance.py) | 3 | P01–P08；状态、恢复与成本 |
| [tests/primary/test_action_role_permissions.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_action_role_permissions.py) | 7 | L01–L03/R03 |
| [tests/primary/test_activation_pool.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_activation_pool.py) | 18 | S03–S06 |
| [tests/primary/test_agent_persistence_semantics_v4.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_agent_persistence_semantics_v4.py) | 4 | P01–P08；状态、恢复与成本 |
| [tests/primary/test_agent_thread_store.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_agent_thread_store.py) | 12 | M01–M05/Q03 |
| [tests/primary/test_builtin_explicit_transactions.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_builtin_explicit_transactions.py) | 5 | B01–B04/P01–P02 |
| [tests/primary/test_builtin_persistence_semantics_v4.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_builtin_persistence_semantics_v4.py) | 5 | B01–B04/P01–P02 |
| [tests/primary/test_checkpoint_component_read.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_checkpoint_component_read.py) | 2 | P01–P08；状态、恢复与成本 |
| [tests/primary/test_checkpoint_memory_pairing.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_checkpoint_memory_pairing.py) | 10 | M01–M10/R04 |
| [tests/primary/test_checkpoint_records.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_checkpoint_records.py) | 27 | P01–P08；状态、恢复与成本 |
| [tests/primary/test_embedding_thread_trace.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_embedding_thread_trace.py) | 5 | M01–M10/R04 |
| [tests/primary/test_env_tick_hooks.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_env_tick_hooks.py) | 6 | E01–E04/S01 |
| [tests/primary/test_explicit_state_transaction_concurrency.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_explicit_state_transaction_concurrency.py) | 26 | P01–P08；状态、恢复与成本 |
| [tests/primary/test_explicit_state_transactions.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_explicit_state_transactions.py) | 23 | P01–P08；状态、恢复与成本 |
| [tests/primary/test_external_environment_injection.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_external_environment_injection.py) | 8 | E01–E04/S01 |
| [tests/primary/test_incremental_checkpoint_v4.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_incremental_checkpoint_v4.py) | 8 | P01–P08；状态、恢复与成本 |
| [tests/primary/test_incremental_checkpoint_v4_branch_gc.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_incremental_checkpoint_v4_branch_gc.py) | 5 | P01–P08；状态、恢复与成本 |
| [tests/primary/test_incremental_checkpoint_v4_process_crash.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_incremental_checkpoint_v4_process_crash.py) | 1 | P01–P08；状态、恢复与成本 |
| [tests/primary/test_incremental_checkpoint_v4_state_machine.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_incremental_checkpoint_v4_state_machine.py) | 2 | P01–P08；状态、恢复与成本 |
| [tests/primary/test_lazy_runtime_imports.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_lazy_runtime_imports.py) | 1 | B01/E01 |
| [tests/primary/test_memory_epoch_sharing.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_memory_epoch_sharing.py) | 3 | M01–M10/R04 |
| [tests/primary/test_memory_receipt_recovery.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_memory_receipt_recovery.py) | 2 | M01–M10/R04 |
| [tests/primary/test_memory_visibility_v4.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_memory_visibility_v4.py) | 10 | M01–M10/R04 |
| [tests/primary/test_observation.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_observation.py) | 12 | Q01–Q05 |
| [tests/primary/test_observation_compact.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_observation_compact.py) | 18 | Q01–Q05 |
| [tests/primary/test_observation_http_independent.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_observation_http_independent.py) | 3 | Q01–Q05 |
| [tests/primary/test_observation_review.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_observation_review.py) | 12 | Q01–Q05 |
| [tests/primary/test_persistence_architecture_v4.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_persistence_architecture_v4.py) | 1 | P01–P08；状态、恢复与成本 |
| [tests/primary/test_persistence_declarations_v4.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_persistence_declarations_v4.py) | 18 | P01–P08；状态、恢复与成本 |
| [tests/primary/test_persistence_lazy_chroma.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_persistence_lazy_chroma.py) | 5 | P01–P08；状态、恢复与成本 |
| [tests/primary/test_persistence_manager_v4.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_persistence_manager_v4.py) | 18 | P01–P08；状态、恢复与成本 |
| [tests/primary/test_persistence_schema_lookup.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_persistence_schema_lookup.py) | 4 | P01–P08；状态、恢复与成本 |
| [tests/primary/test_query_storage_independent.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_query_storage_independent.py) | 1 | Q01–Q05 |
| [tests/primary/test_real_action_contract.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_real_action_contract.py) | 1 | L/M/R/P/Q；真实环境合同，默认不执行 |
| [tests/primary/test_real_e2e_endpoint_config.py](../../tests/primary/test_real_e2e_endpoint_config.py) | 3 | L/M/R/P/Q；真实环境合同，默认不执行 |
| [tests/primary/test_result_datasets.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_result_datasets.py) | 9 | O01–O04/Q03 |
| [tests/primary/test_runtime_checkpoint_v4.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_runtime_checkpoint_v4.py) | 8 | P01–P08；状态、恢复与成本 |
| [tests/primary/test_runtime_diagnostics.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_runtime_diagnostics.py) | 4 | O01–O04/Q03 |
| [tests/primary/test_runtime_entrypoint.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_runtime_entrypoint.py) | 2 | P01–P08；状态、恢复与成本 |
| [tests/primary/test_skill_starter.py](../../tests/primary/test_skill_starter.py) | 4 | U01–U04 |
| [tests/primary/test_social_network_recommendation.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_social_network_recommendation.py) | 22 | B03–B05 |
| [tests/primary/test_society0_primary.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_society0_primary.py) | 171 | A/L/M/R/O/S；逐条循环细节见 2.2 |
| [tests/primary/test_state_persistence_api_v4.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_state_persistence_api_v4.py) | 24 | P01–P08；状态、恢复与成本 |
| [tests/primary/test_state_proxy_collections.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_state_proxy_collections.py) | 8 | P01–P08；状态、恢复与成本 |
| [tests/primary/test_step_recovery.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_step_recovery.py) | 14 | P01–P08；状态、恢复与成本 |
| [tests/primary/test_step_runtime_scope.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_step_runtime_scope.py) | 4 | S03–S06 |
| [tests/primary/test_storage_review.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_storage_review.py) | 12 | P01–P08；状态、恢复与成本 |
| [tests/primary/test_storage_v3_independent.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_storage_v3_independent.py) | 5 | P01–P08；状态、恢复与成本 |
| [tests/primary/test_summary_storage_independent.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_summary_storage_independent.py) | 1 | O01–O04/Q03 |
| [tests/primary/test_thread_integrity.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_thread_integrity.py) | 7 | M01–M05/Q03 |
| [tests/primary/test_thread_native_memory.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_thread_native_memory.py) | 13 | M01–M10/R04 |
| [tests/primary/test_thread_observation.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_thread_observation.py) | 13 | Q01–Q05 |
| [tests/primary/test_tool_choice_policy.py](https://github.com/lavapapa/society0/blob/96b1f3b11aee3c146f5b43e0b8158ec29294e98a/tests/primary/test_tool_choice_policy.py) | 5 | L01–L03/R03 |
| [tests/primary/test_workbench_renderer.py](../../tests/primary/test_workbench_renderer.py) | 3 | U01–U04 |

工作台另有 `tools/workbench-template/tests/model.test.mjs` 与 `render.test.mjs`，覆盖 U03；Python pytest 不会执行这些 Node 测试。`tests/performance/` 也不在默认 pytest.ini testpaths 内，性能验收需显式调用并记录负载。

本清单将能力归属与具体实现分开。后续以 PRD 的共享环境和插件合同为目标，迁移可验证的行为，再用完整原文、逐条行动和跨进程恢复对照判断新版是否真正覆盖旧版。

### 6.1 新版证据

本节按上文ID定位新版实际消费者，保留基线事实与接口迁移的区别。测试链接证明其指定场景，整项能力仍需结合右栏缺口、最终确定性全量与真实服务验收。新版不继承旧裁剪策略和任意Python别名合同。

| ID | 新版实际测试入口 | 当前范围与缺口 |
|---|---|---|
| A01 | [test_kernel_actors.py::test_actor_selector_continues_after_activation_thread_writes](../../tests/primary/test_kernel_actors.py#L8) | Actor持久身份与停用保历史已有测试；临时reminder由Session.signals，最终pilot核对。 |
| A02 | [test_kernel_schedule.py::test_schedule_lazy_selector_sync_rule_and_real_interview_driver](../../tests/primary/test_kernel_schedule.py#L164) | 已有直接测试；最终全量与真实组合另验。 |
| A03 | [test_kernel_cognition.py::test_default_cognition_and_actual_memory_keep_system_first_and_measurement_read_only](../../tests/primary/test_kernel_cognition.py#L108) | 已有直接测试；最终全量与真实组合另验。 |
| A04 | [test_kernel_llm.py::test_reactivation_same_thread_complete_history_and_provider_session](../../tests/primary/test_kernel_llm.py#L196) | 已有直接测试；最终全量与真实组合另验。 |
| A05 | [test_kernel_llm.py::test_long_thread_never_projects_a_bounded_message_window](../../tests/primary/test_kernel_llm.py#L384) | 已有直接测试；最终全量与真实组合另验。 |
| A06 | [test_kernel_llm.py::test_interview_tools_are_measurement_only](../../tests/primary/test_kernel_llm.py#L490) | 已有直接测试；最终全量与真实组合另验。 |
| A07 | [test_kernel_model_selection.py::test_driver_activation_override_preserves_same_thread_and_provider_session](../../tests/primary/test_kernel_model_selection.py#L22) | 已有直接测试；最终全量与真实组合另验。 |
| A08 | [test_kernel_selection.py::test_real_interview_submit_result_aggregates_explicit_nested_field](../../tests/primary/test_kernel_selection.py#L60) | 索引选择/predicate/reservoir/真实访谈嵌套聚合已直接验证；新抽样算法按输入顺序返回。 |
| L01 | [test_kernel_llm.py::test_exact_selection_does_not_expand_same_named_tag](../../tests/primary/test_kernel_llm.py#L369) | 已有直接测试；最终全量与真实组合另验。 |
| L02 | [test_kernel_llm.py::test_invalid_domain_schema_consumes_attempt_without_effect](../../tests/primary/test_kernel_llm.py#L171) | 已有直接测试；最终全量与真实组合另验。 |
| L03 | [test_kernel_llm.py::test_noncompleted_terminal_result_does_not_complete_activation](../../tests/primary/test_kernel_llm.py#L93) | 已有直接测试；最终全量与真实组合另验。 |
| L04 | [test_kernel_llm.py::test_natural_finish_preserves_complete_input_and_thread](../../tests/primary/test_kernel_llm.py#L68) | 已有直接测试；最终全量与真实组合另验。 |
| L05 | [test_kernel_llm.py::test_batch_budget_rejects_all_before_first_domain_effect](../../tests/primary/test_kernel_llm.py#L133) | 已有直接测试；最终全量与真实组合另验。 |
| L06 | [test_kernel_llm.py::test_duplicate_call_id_reuses_receipt_without_budget_or_effect](../../tests/primary/test_kernel_llm.py#L144) | 已有直接测试；最终全量与真实组合另验。 |
| L07 | [test_kernel_llm.py::test_parallel_false_has_one_corrective_turn_then_contract_failure](../../tests/primary/test_kernel_llm.py#L158) | 已有直接测试；最终全量与真实组合另验。 |
| L08 | [test_kernel_llm.py::test_hard_limit_never_executes_truncated_tool_or_requests_closing](../../tests/primary/test_kernel_llm.py#L117) | 已有直接测试；最终全量与真实组合另验。 |
| L09 | [test_kernel_llm.py::test_empty_retry_then_exhaustion_without_domain_calls](../../tests/primary/test_kernel_llm.py#L301) | 已有直接测试；最终全量与真实组合另验。 |
| L10 | [test_kernel_llm.py::test_fact_union_keeps_full_read_text_and_changed_write_resets_only_coverage](../../tests/primary/test_kernel_llm.py#L590) | 已有直接测试；最终全量与真实组合另验。 |
| L11 | [test_kernel_llm.py::test_structured_submit_result_schema_and_terminal](../../tests/primary/test_kernel_llm.py#L213) | 已有直接测试；最终全量与真实组合另验。 |
| L12 | [test_kernel_llm.py::test_two_concurrent_actors_have_task_local_action_call_ids](../../tests/primary/test_kernel_llm.py#L617) | 已有直接测试；最终全量与真实组合另验。 |
| M01 | [test_kernel_threads.py::test_original_messages_requests_and_continuation](../../tests/primary/test_kernel_threads.py#L14) | 已有直接测试；最终全量与真实组合另验。 |
| M02 | [test_kernel_thread_publication.py::test_thread_publication_step_survives_fork_and_new_incomplete_work](../../tests/primary/test_kernel_thread_publication.py#L6) | 已有直接测试；最终全量与真实组合另验。 |
| M03 | [test_kernel_memory.py::test_three_memory_switches_are_independent](../../tests/primary/test_kernel_memory.py#L133) | 单主体八组合及单服务多主体/逐激活冻结策略已验；test_kernel_memory_activation_review 另验同主体异scope拒绝、日期时点与恢复后步骤版本。 |
| M04 | [test_kernel_memory.py::test_thread_extractor_preserves_history_and_protocol_boundary](../../tests/primary/test_kernel_memory.py#L292) | 已有直接测试；最终全量与真实组合另验。 |
| M05 | [test_kernel_memory.py::test_index_failure_retries_saved_vectors_and_receipt](../../tests/primary/test_kernel_memory.py#L86) | 已有直接测试；最终全量与真实组合另验。 |
| M06 | [test_kernel_memory_transfer.py::test_export_import_and_fork_keep_values_without_reembedding](../../tests/primary/test_kernel_memory_transfer.py#L11) | 已有直接测试；最终全量与真实组合另验。 |
| M07 | [test_kernel_memory.py::test_recall_matches_existing_distance_decay_dedup_semantics](../../tests/primary/test_kernel_memory.py#L101) | 已有直接测试；最终全量与真实组合另验。 |
| M08 | [test_kernel_memory.py::test_historical_recall_uses_visible_versions_and_original_vectors](../../tests/primary/test_kernel_memory.py#L269) | 已有直接测试；最终全量与真实组合另验。 |
| M09 | [test_kernel_embedding_provider.py::test_shared_physical_batch_preserves_duplicate_texts_and_actor_provenance](../../tests/primary/test_kernel_embedding_provider.py#L11) | 已有直接测试；最终全量与真实组合另验。 |
| M10 | [test_kernel_memory_transfer.py::test_export_import_and_fork_keep_values_without_reembedding](../../tests/primary/test_kernel_memory_transfer.py#L11) | 转移/seed/close和真实Chroma候选对照已有直接测试；完整真实提供方退出恢复仍待最终阶段。 |
| E01 | [test_kernel_composition.py::test_plugin_schemas_initialize_in_dependency_order_and_share_store](../../tests/primary/test_kernel_composition.py#L8) | schema/初始化/资源依赖由Plugin声明；领域配置校验由机制拥有，旧统一state_schema接口已重设。 |
| E02 | [test_kernel_interaction.py::test_action_registry_is_type_sized_and_rechecks_current_conditions](../../tests/primary/test_kernel_interaction.py#L211) | 已有直接测试；最终全量与真实组合另验。 |
| E03 | [test_kernel_preparation.py::test_prepare_value_released_before_running_and_restore_skips_loader](../../tests/primary/test_kernel_preparation.py#L15) | 异步prepare→同步根事务；before/after另见kernel_schedule，临时作用域见kernel_runtime。 |
| E04 | [test_kernel_graph_plugin.py::test_async_graph_source_and_numeric_projection_restore_without_source](../../tests/primary/test_kernel_graph_plugin.py#L8) | 已有直接测试；最终全量与真实组合另验。 |
| S01 | [test_kernel_schedule.py::test_schedule_hooks_serial_visibility_results_and_single_completion](../../tests/primary/test_kernel_schedule.py#L11) | 已有直接测试；最终全量与真实组合另验。 |
| S02 | [test_kernel_llm.py::test_direct_json_measurement_has_no_domain_tools](../../tests/primary/test_kernel_llm.py#L413) | 已有直接测试；最终全量与真实组合另验。 |
| S03 | [test_kernel_runtime.py::test_collect_preserves_ordered_results_and_drain_consumes_once](../../tests/primary/test_kernel_runtime.py#L392) | 新增Phase.capacity优先于Runtime.capacity（默认1），serial固定1，模型槽位独立；实际ModelProvider端点槽位与phase覆盖已独立验证，联组及独立真实SDK容量消费者已通过，最终全量另验。 |
| S04 | [test_kernel_runtime.py::test_signal_merge_followup_mutual_exclusion_and_cursor_retention](../../tests/primary/test_kernel_runtime.py#L83) | 已有直接测试；最终全量与真实组合另验。 |
| S05 | [test_kernel_schedule_review.py::test_review_second_close_cancellation_does_not_abandon_driver_cleanup](../../tests/primary/test_kernel_schedule_review.py#L10) | 含真实Host重复取消drain；旧activation pool底层测试仍需最终全量。 |
| S06 | [test_kernel_runtime.py::test_phase_order_prepare_before_drivers_and_clock_does_not_advance_on_await](../../tests/primary/test_kernel_runtime.py#L205) | 阶段顺序、同moment重激活/await时间语义已验；未承诺通用离散事件调度器。 |
| B01 | [test_plugin_plain.py::test_plain_plugin_has_no_domain_state_and_restores](../../tests/primary/test_plugin_plain.py#L8) | plain最小机制与干净base Rule/Runtime/restore已验；typed键、顺序与小状态合同由P01实际插件消费者验证。 |
| B02 | [test_plugin_round_robin.py::test_four_person_circle_pairs_each_pair_once_and_restores](../../tests/primary/test_plugin_round_robin.py#L15) | 已有直接测试；最终全量与真实组合另验。 |
| B03 | [test_plugin_social.py::test_social_actions_facts_projection_notifications_restore](../../tests/primary/test_plugin_social.py#L15) | 已有直接测试；最终全量与真实组合另验。 |
| B04 | [test_plugin_social.py::test_social_active_pool_and_scores_match_old_semantics_without_body_scan](../../tests/primary/test_plugin_social.py#L119) | 已有直接测试；最终全量与真实组合另验。 |
| B05 | [test_plugin_social.py::test_social_two_instances_original_body_and_rejections](../../tests/primary/test_plugin_social.py#L47) | 已有直接测试；最终全量与真实组合另验。 |
| N01 | [test_kernel_interaction.py::test_action_registry_is_type_sized_and_rechecks_current_conditions](../../tests/primary/test_kernel_interaction.py#L211) | 已有直接测试；最终全量与真实组合另验。 |
| N02 | [test_kernel_interaction.py::test_discover_read_and_invoke_are_independent](../../tests/primary/test_kernel_interaction.py#L105) | 已有直接测试；最终全量与真实组合另验。 |
| N03 | [test_kernel_information_sql.py::test_authorized_projection_filter_count_and_refs](../../tests/primary/test_kernel_information_sql.py#L34) | document/dataset/query/sample及认知增量均已有直接测试；push实际领域内容由CognitiveInput消费者验。 |
| N04 | [test_kernel_workspace.py::test_shared_world_mount_is_dynamic_readonly_and_authorized](../../tests/primary/test_kernel_workspace.py#L62) | 动态World挂载、增量私有workspace及跨平台wheel已验；真实模型自主使用效果待V06。 |
| N05 | [test_kernel_plugins_review.py::test_review_context_is_expired_after_host_closes](../../tests/primary/test_kernel_plugins_review.py#L11) | 已有直接测试；最终全量与真实组合另验。 |
| R01 | [test_kernel_models.py::test_profiles_install_through_host_and_preserve_request_defaults](../../tests/primary/test_kernel_models.py#L40) | 旧成熟适配器继续消费；新版真实多端点最终矩阵待V06，不以fake SDK代替。 |
| R02 | [test_kernel_models.py::test_model_close_drains_request_and_rejects_later_requests](../../tests/primary/test_kernel_models.py#L102) | 已有直接测试；最终全量与真实组合另验。 |
| R03 | [test_kernel_provider_fields.py::test_tool_call_extensions_survive_normalized_response_and_next_request](../../tests/primary/test_kernel_provider_fields.py#L10) | 额外SDK字段下一请求全等；strict/parallel/tool-choice另见LLM与旧manager直接组。 |
| R04 | [test_kernel_embedding_provider.py::test_embedding_profile_controls_http_capacity_and_batching](../../tests/primary/test_kernel_embedding_provider.py#L95) | 已有直接测试；最终全量与真实组合另验。 |
| R05 | [test_kernel_models_review.py::test_review_retry_evidence_preserves_actual_request_when_thread_advances](../../tests/primary/test_kernel_models_review.py#L12) | 请求水位/轻诊断已有独立例；凭据脱敏与proxy沿成熟manager测试最终复验。 |
| O01 | [test_kernel_results.py::test_results_preserve_all_fields_stream_table_once_and_restore](../../tests/primary/test_kernel_results.py#L9) | JSON行/生成器/巨行与恢复已验；test_kernel_result_inputs 新增显式TableValue/DatasetTable，真实pandas tight完整恢复见 result-dataframe-consumer-green；新增组待独立复验。 |
| O02 | [test_kernel_usage.py::test_real_adapters_project_retry_response_and_shared_cache](../../tests/primary/test_kernel_usage.py#L82) | 物理调用、实际行动/终止、阶段时长已有作者及独立消费者；test_kernel_action_timing_review验证重试不重复、complete只读视图与失败live诊断，最终全量另验。 |
| O03 | [test_kernel_results.py::test_result_page_encodes_each_item_once_and_fixed_prefix](../../tests/primary/test_kernel_results.py#L64) | 已有直接测试；最终全量与真实组合另验。 |
| O04 | [test_kernel_schedule.py::test_progress_io_failure_isolated_but_result_generator_failure_aborts](../../tests/primary/test_kernel_schedule.py#L44) | 已有直接测试；最终全量与真实组合另验。 |
| P01 | [test_kernel_record_plugin.py::test_record_projection_and_fact_partial_transaction_rolls_back](../../tests/primary/test_kernel_record_plugin.py#L30) | typed_records真实插件已作者验证公开append/唯一typed键/ordinal/精度/恢复；受信writer可修改SQL，未声称数据库自动禁止作者更改事实；typed_records独立5项通过。 |
| P02 | [test_kernel_record_plugin.py::test_record_projection_and_fact_partial_transaction_rolls_back](../../tests/primary/test_kernel_record_plugin.py#L30) | 规范writer事务/租期失效与事实+投影复合回滚已有实际消费者；显式插件接口替代旧raw dict别名；typed_records独立消费者已通过。 |
| P03 | [test_kernel_storage.py::test_process_crash_publication_boundary](../../tests/primary/test_kernel_storage.py#L162) | root/native changeset/complete边界已验；每步完整点替代旧多步epoch，重数据实测见V04。 |
| P04 | [test_kernel_memory.py::test_pending_job_full_checkpoint_resumes_without_extraction](../../tests/primary/test_kernel_memory.py#L61) | 已有直接测试；最终全量与真实组合另验。 |
| P05 | [test_kernel_storage_lifecycle.py::test_restore_copies_repeated_artifact_once_and_survives_source_removal](../../tests/primary/test_kernel_storage_lifecycle.py#L10) | fork脱源恢复、GC可达验证已验；native root复制成本显式保留，未承诺零复制fork。 |
| P06 | [test_kernel_storage_lifecycle.py::test_foreign_root_identity_rejects_export_and_gc_without_deleting_files](../../tests/primary/test_kernel_storage_lifecycle.py#L73) | prepare_readonly 已由 test_kernel_analysis_export 跨Thread/Memory原向量/Results/Dataset实测源删除后独立读取，拒绝writer/restore；无需额外导出包装；Results/Dataset/Workbench 独立贯穿已通过，见 result-inputs-independent-consumer-green-20261004。 |
| P07 | [test_kernel_storage.py::test_incremental_blob_read_bounded_and_scope_checked](../../tests/primary/test_kernel_storage.py#L313) | SQLite BLOB/Thread块范围与压缩流已验；实际大root下界由V04实测，不承诺任意JSON单值恒定内存。 |
| P08 | [test_kernel_runtime.py::test_failed_driver_aborts_and_does_not_retry_successful_action](../../tests/primary/test_kernel_runtime.py#L143) | 已有直接测试；最终全量与真实组合另验。 |
| Q01 | [test_kernel_observation.py::test_separate_producer_observer_tracks_pending_failure_without_model_import](../../tests/primary/test_kernel_observation.py#L83) | 已有直接测试；最终全量与真实组合另验。 |
| Q02 | [test_kernel_observation.py::test_cli_rebuilt_same_complete_identity_continues_cursor_and_large_reference](../../tests/primary/test_kernel_observation.py#L260) | 已有直接测试；最终全量与真实组合另验。 |
| Q03 | [test_kernel_observation.py::test_observer_live_tail_complete_prefix_and_fork_identity](../../tests/primary/test_kernel_observation.py#L9) | 已有直接测试；最终全量与真实组合另验。 |
| Q04 | [test_kernel_observation.py::test_prepare_busy_killed_process_keeps_ready_and_clear_reclaims_owned_files](../../tests/primary/test_kernel_observation.py#L148) | 新版权威SQL替代全World派生索引；固定完整点prepare使用prepare_readonly物化只读SQL，成本另测。 |
| Q05 | [test_kernel_observation_http.py::test_http_slow_body_saturation_disconnect_and_final_wire_bytes](../../tests/primary/test_kernel_observation_http.py#L19) | 已有直接测试；最终全量与真实组合另验。 |
| Q06 | [test_kernel_observation.py::test_observer_live_tail_complete_prefix_and_fork_identity](../../tests/primary/test_kernel_observation.py#L9) | live/current与完整点、Thread tail已有；产品语义以新Observation合同为准，不伪造额外sealed水位。 |
| U01 | [test_kernel_runner.py::test_runner_freezes_manifest_and_timings_and_restores](../../tests/primary/test_kernel_runner.py) | 研究引导和技能参考已迁移至共享环境/正式插件；RunPlan 消费者验证配置清单、完整步骤与恢复，真实提供方及最终文档审查仍由 V05/V07 验收。 |
| U02 | [test_skill_starter.py::test_starter_preflight_and_two_llm_steps_keep_current_information_without_memory](../../tests/primary/test_skill_starter.py) | 正式工厂两轮 LLM starter 已确定性验证完整材料、显式提取和访谈；独立 test_skill_starter_review 验实际资源预检零调用与非空经验；新预算/依赖清单另有直接测试。真实网络运行单独留证。 |
| U03 | [test_core_next_workbench.py::test_actual_result_rows_and_metrics_have_table_and_chart_views](../../tests/primary/test_core_next_workbench.py) | 真实 Results 表格/指标曲线及已有 React 渲染消费者已验；同名 phase 按 ordinal 分开，实体 namespace 与显式配置版本有独立用例。 |
| U04 | [test_core_next_workbench.py::test_real_pilot_export_and_existing_single_file_renderer](../../tests/primary/test_core_next_workbench.py) | 正式 pilot→完整点→新 workbench payload→既有单文件 renderer 已贯穿；巨行/原始 Thread 范围及 CLI 选取实际 run 另有消费者。 |
| U05 | [test_public_core_api.py](../../tests/primary/test_public_core_api.py) | 新公共入口和可选依赖惰性导入有直接测试；macOS/Linux wheel 与独立轻安装证据见 installation.md。旧 WorkBuddy 案例保历史身份，新版未另行声称通过 WorkBuddy 场景。 |

这份索引将尚缺的领域合同、公开入口与真实组合保留为待办。补齐对应消费者和最终验收后，再据实际证据调整迁移结论。

两轮研究 starter 的召回提供方故障会使当前步骤失败并保留此前完整点；旧 starter 静默继续测量的行为已撤销。成功返回空记忆仍继续，当前消息原文仍在输入中；无召回实验应显式关闭 auto_recall。这项语义调整区分了服务故障与合法的空记忆条件。

终审补充：目录可发现性与处理器异常的步骤失败边界由 `test_core_final_review.py` 两个非作者反例及 `test_kernel_final_boundaries.py` 覆盖。前者约束信息名称和 total，后者保证已写部分事实的 handler 异常即使被 Driver 捕获也不能发布完整点；本组最终审查与全量结果单独记录。
