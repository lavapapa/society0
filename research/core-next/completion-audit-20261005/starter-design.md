# 正式 starter 真实验收

直接加载 skill/assets/minimal_experiment.py 的 build_plan，经正式 run_plan 连续执行完整 moments=(1,2)，随后以原计划从可信 step=1 在新目录恢复第二轮。保留示例的完整原文、自动写入／召回／主动工具、LLMPolicy 原预算及提供方默认重试。指定 SiliconFlow Qwen/Qwen3.8-27B 与 Qwen/Qwen3-Embedding-0.6B，1024维；60秒端点期限、max_tokens=1024、temperature=0、parallel_tool_calls=false、tool_choice=auto、extra_body.enable_thinking=false及SDK连续流累计用量合同与既有验收一致。

成功标准：两条路线发布 step=2；第一轮 news.view_details 完成且权威查看事实恰好一条；第一轮经验保存为 alice/timestamp=1 的 ready 记忆并保存1024维原向量；完整Thread保留原消息；第二轮返回completed的1–7整数可信度与非空理由；恢复不重复第一轮行动或经验，继承的完整事实与新增物理调用分别留证。随机模型的访谈措辞不要求连续与恢复逐字相等。协议失败、超时、截断及硬预算结束均保留诊断，不能判定成功。

预计连续加恢复约6–14次模型及3–6次嵌入请求，正式starter无有限max_turns，实际纠错与默认重试可能增加。主控观察运行状态和物理调用，超过30次独立物理请求或持续等待时先诊断并决定停止本次研究；不修改主体预算或静默标记成功。runner使用getpass无回显输入，凭据仅在进程内，原文工件保留在本地输出目录。
