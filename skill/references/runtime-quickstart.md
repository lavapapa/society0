# 首次实验的技术起步

首次接触运行目录、结果与恢复时，先完成 [无模型起步](../../docs/core-next/getting-started.md)。复制 `../assets/minimal_experiment.py` 到本次实验版本目录。示例使用正式插件装配、持久主体、LLMDriver 和代码调度，依次完成查看详情、在原 Thread 关闭前显式提取经验、下一轮结构化访谈。空记忆不移除当前消息；提供方失败保留未完成诊断并阻止步骤发布。

## 一、配置

先核对实际解释器、Society0 源码提交和依赖固定。基础规则运行使用 SQLite 与 JSON schema；本例还需要 llm、memory 依赖组，可在源码工作树执行 `uv sync --extra llm --extra memory`。嵌入服务用于本例的记忆设计；无记忆的 LLM 计划按所用机制配置。模型、嵌入、向量索引和 Thread 服务通过插件显式依赖装配。Actor 的安装期 driver_factory 取得共享服务，实际激活再构建轻量 Driver。

示例从运行环境读取 SOCIETY0_RELEASE、SOCIETY0_LLM_MODEL、SOCIETY0_LLM_BASE_URL、SOCIETY0_LLM_API_KEY、SOCIETY0_EMBED_MODEL、SOCIETY0_EMBED_BASE_URL、SOCIETY0_EMBED_API_KEY、SOCIETY0_EMBED_DIMENSIONS；可选 SOCIETY0_LLM_OPTIONS 是提供方已验证请求选项的 JSON。凭据留在进程环境。根据实际模型设置推理、工具选择与输出预算，保留原研究合同，截断明确记为未完成。

## 二、运行

```sh
python versions/v001/experiment.py --check --run-dir versions/v001/runs/check-001
python versions/v001/experiment.py --run-dir versions/v001/runs/pilot-001
```

检查模式创建真实初始完整点、安装服务并关闭资源，不调用提供方。它验证初始化与装配，真实服务连接、动作、记忆和测量通过后续小规模 pilot 验证。运行前保存本次实验源码、无凭据配置与依赖身份，每次尝试使用新目录。`runner.json` 保存公开运行合同，`runner-status.json` 报告状态与完整步骤号，`timings.jsonl` 保存阶段数字。

## 三、检查

使用 Observation 读取 Thread 原文、行动结果、资源用量、阶段表与完整步骤。模型访问的信息、目录上的条件标签与研究者分析标签分别核对。访谈只作测量；实际业务状态改变由 action 实现，成功受理与业务完成分开判断。记忆的自动写入、自动召回和主动工具分别配置；本例关闭自动写入，通过研究自定义的完成钩子显式提取浏览经历。服务异常继续传播，研究者可按可信完整点另建恢复运行。此 starter 的 `build_plan(moments=...)` 接受完整业务时间序列，恢复时从已完成步骤之后继续；恢复通过 `run_plan(new_dir, plan, source=old_dir, step=...)` 明确指定来源，调用合同见 [运行入口](../../docs/core-next/runner-contract.md)。

方法分析见 `run-monitor-analyze.md`，调度与结果输入见 `step-dsl.md`。低成本试验通过减少主体或重复次数保持研究对照完整，完整上下文和仍可执行的动作继续可达。
