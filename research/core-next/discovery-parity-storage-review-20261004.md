# 工具说明与旧新对照独立审查

本轮按真实验收中的新发现审查工具说明，并作为第三方核对 V03 的旧、新运行适配器、比较器与保存的完整原文。产品由各作者修改，独立审查仅增加测试和证据；没有模型网络调用，也未重跑旧侧全矩阵。

## 一、说明

LLMDriver 的修改集中在 action_find 工具说明、query 字段说明和 parallel_tool_calls=False 时每项工具的单调用提示。搜索算法、动作集合、参数字段、完成条件及预算保持原值。说明如实表达现有大小写无关字面子串查询、空串列全及 cursor 分页；单调用提示随已选择的策略出现。

作者通过实际 Driver→provider.options 检查两种策略。非作者另用实际 Actions.find 验证空串按一项分页取得全部三项，INVENTORY 匹配两个描述，星号仅匹配含字面星号的描述，带星号的 inventory 字符串无匹配。False 与 True 策略产生同一套参数 schema，连续生成工具不会污染其他策略。独立两项、作者两项和既有 LLM 测试合计 45 通过，见 [说明复验](discovery-description-storage-independent-20261004.txt)。本范围无未解决问题。

## 二、对照

旧适配器确实使用冻结 `96b1f3b` 的 World/proxy、execute_action_loop、Memory 和 PersistenceManager；独立核对本地旧源码中五个关键文件均与该提交逐字一致。新适配器使用 compose、Runtime、Actions、LLMDriver、ThreadStore 和 SQL 权威 Memory。两主体、三步的固定业务输入分别运行规则与模型替身路线，持续和完整点恢复形成八个被比较结果，另外保存四个一步前缀。旧侧恢复通过目标目录 Observation 检查 Thread，新侧恢复后直接检查原 thread id 的完整消息；两侧均从完整状态继续。

比较器对 initial、每步 snapshots、全部 decision_inputs 和 memories 逐值比较，含资金、累计投影、按序事实、完整说明、召回内容和四维原向量。新旧外层协议不同，各自保持完整消息。非作者从保存的一步原文独立检查恢复后的业务前缀、全部实际提供方输入前缀和两个 Thread 的全部消息，均相等。证据在 [前缀与来源核对](parity-storage-prefix-source-20261004.json)。

首次审查发现比较器对 Thread 仅核 tool 包含公共 NOTE，对实际 provider input 仅解析 PARITY 材料。八个独立故意损坏用例分别修改 old/new 恢复组的 system、assistant、保留 NOTE 的 tool 金额以及 provider system，全部被旧比较器错误接受，红证据保存在 [遗漏复现](parity-storage-independent-red-20261004.txt)。保存的原始工件恰好全等，仍需要修正比较器才能守住后续验收。

作者现增加同版本 continuous/restored 的全部 Thread messages 比较，仅去掉外层随机 thread id、保留原顺序与消息内全部字段；全部 provider_inputs 原文直接逐值比较。八个原独立用例保持不变，连同作者五项共 13 通过，见 [修复独立复验](parity-storage-independent-green-20261004.txt)。本次发现已关闭，未修改原始运行材料来使比较通过。

## 三、边界

V03 证明固定账务 fixture 在实际旧、新写入与行动循环下的结果、完整认知材料及完整点恢复保持所列语义。规则旧侧直接调用 World 代理与持久化；模型侧采用确定性提供方，Memory 写入使用明确固定条目和固定向量。因此它没有覆盖真实产业全模型迁移、真实推理等价性、模型提取质量或所有旧环境行为；这些成本与研究判断分别由领域验收和真实提供方测试承担。

工具说明修复和 V03 比较器当前审查范围均无未解决阻断，证据可以冻结。真实网络整组仍需使用最后候选身份复跑；本报告不把固定提供方的逐值对照替代实际端点验收。
