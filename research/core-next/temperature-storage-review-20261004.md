# 可选温度策略独立复核

本次核对 L09/L10 既有可选能力及 R05 既有递归配置过滤。提供方由实际 ModelProvider 加 SDK 替身执行，全程没有模型网络请求。测试依据当前候选源码；最终冻结提交后的全量验收另行记录。

## 一、温度策略

独立用例验证空响应计数继续约束整个激活预算，而温度调整仅作用于紧接空响应的下一请求。空响应后出现普通工具行动，再次空响应超过原预算时结束；正常工具后的请求恢复原始温度，调温事件没有重复泄漏。

重复事实策略的两个早退消费者分别是尚未满足必需行动的纯正文与违反单调用合同的工具集合。它们都重置重复读取连续计数，后续请求恢复基值；旧消息逐值保持为后续请求前缀。作者另覆盖新事实、修改、无事实读取、同 Thread 再激活和默认关闭。

实际 ModelProvider 消费者验证 profile 默认温度 0.7、激活空响应调整后 0.9、提供方物理超时重试仍为 0.9，两个物理请求保留相同完整消息与持久请求选项。policy 显式选项继续覆盖 profile 默认；每轮选项为局部副本。空响应按原始基值加一次 delta 封顶，重复读取按连续次数加 delta 封顶；均未增加激活预算或强制收尾。

## 二、凭据合同

R05 独立三项测试已保存于 credentials-storage-independent-20261004.txt，并加入本次联合复验。既有递归 mapping/list 敏感配置过滤保留非凭据内容及输入对象；实际请求配置、成功原始响应与 HTTP 错误证据经 Thread 保存和完整步骤恢复后逐值一致，合成凭据均未进入持久证据。没有扩大过滤规则或修改产品。

## 三、执行

命令：`PYTHONPATH=src .venv/bin/python -m pytest -o addopts='' -q tests/primary/test_kernel_temperature_review.py tests/primary/test_kernel_empty_retry_temperature.py tests/primary/test_kernel_repeated_read_temperature.py tests/primary/test_kernel_credentials_review.py tests/primary/test_kernel_llm.py`。

退出码 0，63 passed in 2.23s，原始输出 temperature-storage-independent-20261004.txt。新增独立文件 test_kernel_temperature_review.py 包含三个消费者。当前复核范围未发现未关闭的阻断；原 40d385d 全量仍对应先前产品，本次候选需要重新冻结后执行最终组合验收。

## 四、同轮多行动复验

根复查发现同一模型轮先产生新事实或执行 changed 写入，随后重复读取会覆盖本轮已有进展。新增两个实际 parallel_tool_calls=True 消费者分别执行 new/new 与 write/old/old；修复前下一请求温度为 0.5，预期恢复原始 0.1。两项原始红灯保存在 temperature-multi-action-independent-red-20261004.txt。

作者以每轮进展标志保留“本轮出现过新事实或 changed”的状态，后续重复读取无法将其覆盖，下一请求开始清除轮内状态。独立反例保持不变再次执行，联合组 65 passed in 2.30s、退出码 0，输出 temperature-storage-final-independent-20261004.txt。本范围已发现问题全部关闭；温度调整继续使用原预算与完整消息。
