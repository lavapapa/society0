# 真实验收迁移

旧版真实测试以单体 Society0、World 和原动作签名装配。新版真实验收使用共享环境、标准插件与完整步骤恢复。下表逐项保存原 13 项真实测试及追加跨进程测试的研究语义；当前确定性消费者提供机制证据，最终真实提供方执行仍由 V05 记录，表中的映射不代表真实调用已经通过。

## 一、逐项对应

原测试位于基线 `96b1f3b11aee3c146f5b43e0b8158ec29294e98a` 的 `tests/e2e/test_society0_real_e2e.py`。新列给出当前实际测试模块和关键消费者，正式提供方参数、资源并发、记忆与恢复链在同一新运行合同下重新验证。

| 原测试函数（省略 test_real_） | 保留的语义 | 新确定性消费者与真实阶段要求 |
| --- | --- | --- |
| endpoint_smoke_llm_and_embedding | LLM 与每段原文向量实际返回 | `test_kernel_models.py`、`test_kernel_embedding_provider.py`；V05 两类实际提供方 |
| endpoint_saturation_llm_and_embedding_managers | 并发许可及多主体独立输入结果 | `test_kernel_models.py`、`test_kernel_capacity_review.py`；V05 多请求资源占用与错误 |
| society0_interview_e2e_writes_artifacts | 结构化访谈、完整消息与可读产物 | `test_kernel_llm.py::test_structured_submit_result_schema_and_terminal`、`test_kernel_selection.py` 实际访谈聚合 |
| society0_saturation_default_model_concurrency_memory_and_logs | 主体执行与模型资源容量、独立记忆和日志 | `test_kernel_capacity_review.py`、`test_kernel_services.py`、`test_kernel_usage.py`；执行容量按新 Phase 合同，模型槽位独立 |
| society0_explicit_agent_group_concurrency_overrides_model_e2e | 显式主体并发仍受模型请求资源约束 | `test_kernel_capacity_review.py::test_review_phase_override_runs_three_actors_while_provider_allows_one`；新 Phase 优先级替代旧 group/world 接口 |
| society0_memory_roundtrip_e2e | 真实提取、原文向量、下一时点召回、恢复 | `test_kernel_services.py::test_standard_services_two_steps_and_restore`、`test_kernel_memory.py`；V05 实际 LLM/embedding/Chroma |
| explicit_transactions_memory_roundtrip_e2e | 状态与记忆完成边界、重启完整内容 | `test_kernel_storage.py`、`test_kernel_memory.py`、`test_kernel_services.py`；新规范 SQL 写入统一验收，无旧模式切换 |
| society0_round_robin_env_logic_and_llm_action_loop_e2e | 配对及消息原文、动态行动与成功终止 | `test_plugin_round_robin.py`、`test_kernel_llm.py`；V05 真实驱动调用正式轮转机制 |
| society0_social_publish_action_e2e | 发布原文、可见帖子、成功记忆及用量 | `test_plugin_social.py`、`test_kernel_services.py`；V05 正式社会网络插件 |
| society0_environment_action_tag_e2e | required/completion 标签和动作结果 | `test_kernel_llm.py::test_per_action_batch_limit_and_tag_completion`；V05 实际元工具及领域动作 |
| society0_terminal_action_retry_preserves_agent_loop_e2e | 失败终止动作不收尾、成功才终止 | `test_kernel_llm.py::test_noncompleted_terminal_result_does_not_complete_activation`、`test_terminal_completed_ends_without_extra_request`；原工具错误修正由真实响应推进 |
| society0_social_browse_completion_tags_default_memory_e2e | 推荐全候选及原文、曝光、完成标签、记忆 | `test_plugin_social.py` 推荐与曝光、`test_kernel_schedule.py::test_composed_social_instances_automatically_flush_before_complete`；V05 真实浏览后记忆 |
| society0_multi_tick_social_workflow_e2e | 多时点事实演进与完整恢复、原文不裁剪 | `test_plugin_social.py::test_social_runtime_complete_preserves_exposure_and_failed_step_restores_prior_world`、`test_kernel_cognition.py`；V05 多步连续操作 |
| 追加：test_real_exit_restore_memory_and_observation | 独立进程退出后完整状态、Thread、原向量及外部观察 | `test_kernel_cognition.py::test_cognition_cursor_survives_fresh_process`、`test_kernel_observation.py`、Memory 恢复消费者；V05 同一真实链进程退出恢复 |

## 二、比较范围

同一业务输入对应的事实、插入及行动顺序、Thread 中保存的原始消息与记忆正文逐值对照。新 precision、信息查询和 meta-tools 会改变提示组织；验收核对决策所需内容及可执行行为完整可达，同时检查请求未裁剪、分页可继续、总数与原文范围真实。提示结构允许改变的合同不放宽信息完整性。

记忆的自动召回、自动写入和主动工具分别验证。单服务策略的主体与激活级选择已由标准装配、多主体并发及独立跨作用域消费者验证，单主体八组合覆盖开关独立性；真实提供方链继续由 V05 验证。真实测试保留明确模型输出预算及截断失败事实，不用改变预算或额外收尾调用制造成功。

## 三、资产处理

旧运行入口测试从新版测试收集中退役时，以本表与能力矩阵保留对应关系。原实现、旧测试和历史真实失败及通过工件仍可由固定 Git 提交追溯。提供方共享模块的工具参数、重试、取消、超时与批处理直接测试继续运行；算法逐值对照使用 `tests/reference` 固定的最小原始算法。

新版确定性与真实验收分别留证。上述所有真实阶段完成之前，V05 保持未完成状态。主体自主发现信息、使用 VFS、完成行动的效果对照单列 V06，使用完整信息和可行动范围作为判断依据。

两轮研究 starter 的召回提供方故障会使当前步骤失败并保留此前完整点；旧 starter 静默继续测量的行为已撤销。成功返回空记忆仍继续，当前消息原文仍在输入中；无召回实验应显式关闭 auto_recall。这项语义调整区分了服务故障与合法的空记忆条件。
