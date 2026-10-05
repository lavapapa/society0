# 正式 starter 两轮与恢复验收

本次直接加载 skill/assets/minimal_experiment.py 的正式 build_plan，保持原完整输入、LLMPolicy、记忆三个开关与默认重试，经正式 run_plan 连续执行两轮，再从 step=1 的可信完整点在新目录恢复第二轮。提供方为指定 SiliconFlow Qwen/Qwen3.8-27B 与 Qwen/Qwen3-Embedding-0.6B（1024维），端点60秒、max_tokens1024、temperature0、关闭推理、禁止并行工具、自动选择工具与SDK连续用量设置均保留。凭据由主控通过无回显TTY输入，未保存。

预先固定的技术成功标准全部通过。两条路线发布step=2，首轮news.view_details完成且权威查看事实恰好一条；Alice保存三条第一轮ready记忆与1024维原向量，第二轮完整Thread含原消息及实际召回原文。两条访谈均completed，返回可信度2与非空理由。恢复继承第一轮完整Thread、记忆正文和原始向量，逐值相等，没有重做首轮查看事实或经验写入。恢复访谈措辞存在随机差异，采用事先固定的结构与事实状态oracle验收。具体判定见starter-oracle.json。

连续运行15次物理调用，恢复新增2次，按(kind,id)去重合计17次，用时73.457秒；共同历史事实一致性已检查，未达到30次研究观察线。全部计数与提供方实报用量见starter-summary.json，模型与embedding分别来源于thread_usage_calls和resource_usage_calls，恢复副本不重复累计。原始运行、完整Thread和逐路线检查材料保留在本目录的 starter-artifacts/（本地研究工件，不提交数据库正文）；原始 /tmp 副本保留。

最后只读核对当前全部src/society0 Python文件、正式starter与Rust lib.rs，与ab6469d中的原字节相等，文件清单见starter-oracle.json。研究runner及检查器事先用现成test_skill_starter确定性消费者走通，正式profile零请求预检也通过。本次没有修改产品。

实际模型输出出现研究内容风险：输入没有日期，记忆却写入“2025-06-10”；恢复访谈进一步声称类似事件最终未获证实，原消息和记录并未提供该结局。软件按合同保存并召回这些原始内容。本验收支持正式入口、工具、记忆、测量与完整恢复可用性；该次模型输出的事实可信度和研究有效性须单独判断。这项观察不改变原oracle，也没有通过改提示词或重复请求修饰结果。
