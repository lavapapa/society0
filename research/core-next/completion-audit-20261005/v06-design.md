# V06 同源真实任务对照

本试验检验固定报价核对任务在三种实际输入与行动合同下能否完整获取资料、作出相同数值判断并完成提交，同时记录实际任务成本。原始资料来自当前产品 `ab6469d` 的 `real-final-05/test_real_vfs_discovery_pagination_original_and_action` 完成工件；先用现行 `assert_vfs_artifacts` 复验，再读取 SQL 权威表导出 `v06-fixture.json`。共同材料为十二条报价、主体 a 的任务记录及完整 117 字节报告。验收值直接由原表计算，短语保留源句号。

## 一、基线

新组复用本轮既有成功工件，其实际目录发现、四页报价、grep、两段原文读取、jq 复算、动态行动发现与描述及提交均可追溯到完整 Thread。该组版本、原目标、预算和 SDK 条件由工件内 `runner.json` 保留。

结构化旧接口组冻结 `edabcad3e88ba7b7b92c68dcfa5c22bf6f2e304a`，使用该提交的 `core_next_real_support.plan`、`discovery_catalog_plugin`、正式 `LLMDriver` 和提供方工厂。其 data_list、data_query、data_read 能获取同一 SQL 数据集及正文，并已具有 action_find、action_describe、action_invoke。此组比较资料接口；动态行动本身的旧基线由下一组承担。runner 从冻结真实测试直接读取原任务文字，启用其原 workspace 和 bash/jq；保持四页 limit=3、size=64 完整原文、jq 复算、按 actor Ref 发现提交行动及同字段结果。

固定工具集旧组冻结 `96b1f3b` 的实际 World、ActionSet、execute_action_loop。完整原文资料作为原始输入提供，领域消费者仅注册 catalog_submit，并按原行动循环执行。该版所有域工具均消耗 action budget，为保留共同两次行动预算，本组使用旧版已有的完整材料直接输入方式。该组用于核对完整输入、数值判断及固定行动提交；检索路径与新组不同。

## 二、合同

两旧组使用各冻结源码的 `uv.lock` 创建独立环境，指定 SiliconFlow Qwen/Qwen3.8-27B，max_turns=20、max_action_calls=2、单请求 timeout=60 秒、max_tokens=1024、temperature=0、parallel_tool_calls=false、enable_thinking=false。自动记忆与嵌入未启用。旧 World 使用冻结 openai 2.53.0，结构化旧接口使用冻结 openai 3.24.0 与 Pydantic AI 2.54.0；SDK 版本与交互合同差异参与成本解释。结构化请求选择 SDK 现成 continuous_usage 配置以正确处理累计流统计。

runner 的 getpass 从 TTY 隐藏读取凭据，凭据留在进程内存，不写工件或命令参数。每次独立目录保留失败诊断。产品源码保持冻结。研究程序、资料快照及验收证据集中于本目录的 v06 文件。

## 三、验收

先进行无网络确定性消费者检查。结构化组 `v06-structured-dry-06` 通过实际旧驱动及信息接口，四页依次读取原始十二条数值、两段按 revision/offset 拼合完整报告、真实旧 shell 的 jq 复算 12/546，再发现、描述并提交 count=12、total=546、phrase=原文校验成功。冻结原测试 `assert_vfs_artifacts` 和追加完整正文 oracle 均通过。旧 World 组 `v06-world-dry-01` 在 provider 请求入口断言完整资料原文确实存在，再通过实际 ActionSet 与循环完成同提交。

`v06-structured-01` 保留为设计缺陷诊断。它未启用冻结版已有 workspace/jq，完整四页和117字节正文读取成功，最终误算并提交528。其对照条件欠缺，效果比较采用恢复冻结原合同后的02组。早期确定性载体比较与 oracle 接线失败也保留，均未调用真实端点。

真实判定继续要求完整四页数值顺序、总数、continuation、所有 size=64 正文片段的 revision 和 byte offset、原文拼合完全相同，以及唯一提交字段完全相同。固定工具集组检查实际完整输入与唯一提交。真实响应完整性与循环完成状态分别记录。

成本来自实际物理请求、报告的输入/输出/cache token 和墙钟，不以工具数量或返回长度独立判优。本确定题成功可支持三条输入行动路径的结果等价及本次成本差异；样本数量、既有新组运行时点、不同交互合同及不同 SDK 条件限制因果判断，无法推导普遍优化或统计效果。
