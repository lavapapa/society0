# action_find 游标修复记录

本次从已审查的 d860f8e 创建独立分支 task/core-action-cursor-20261021，修复 action_find 对空游标的错误反馈与未充分声明的结构。原工具集合、完整分页、64轮预算及重复响应默认 None 保持原样。

## 一、根因

只读检查110真实运行 parallel-serving-20261021-224644 的 Thread。AESC 第一轮 action_find 使用 cursor={}，原任意对象 schema 接受它，ledger 随后读取 identity 抛出 KeyError。CALB 先猜不存在的行动名，之后同样在空游标上循环。BTR 在发现36个行动后只取返回值的内层 cursor，并同时把 query 从空串改为 adjust，触发 action cursor mismatch；随后尝试将对象改成字符串。错误输入属于模型调用错误，公开 schema 接受空对象且诊断缺少修正方法属于接口缺陷。

修改为：非严格模式允许省略 cursor；对象明确要求外层 identity/cursor、内层 identity/offset，可选 candidates；严格模式保留JSON字符串传输，解码后执行同样的结构验证。结构错误及查询不匹配明确说明首次或新query/target省略cursor或设null，分页须完整原样复制next_cursor。保持原筛选和分页实现。

## 二、验证

直接测试文件为 tests/primary/test_action_cursor_feedback.py，覆盖非严格及严格模式的空对象纠正、首次省略cursor、完整翻页、准确总数、新query复用旧cursor失败及重新查询。与现有 test_kernel_llm.py 共44项通过，git diff --check通过。没有运行全量。

在144 Qwen/Qwen3.6-35B-A3B端点使用真实Thread第一次失败前的完整消息前缀，替换为新schema及规范反馈，每次最多一次响应、1024输出token、auto工具选择、关闭thinking。AESC下一次响应省略cursor并通过schema；BTR下一次仍输出JSON字符串cursor，未通过。另试一次附带具体restart_arguments的反馈仍失败，该额外字段已撤销，最终补丁保留规范反馈。共3次短请求，未执行产业动作，未重新跑产业世界。

远端证据在110:/home/marvin/projects/society0-next-lithium-20261021/evidence/core-action-cursor-20261021/real-correction.json及real-correction-btr-restart.json。后者是已撤销试探的记录。独立源码位于该目录src，交付前同步最终版本。

## 三、缓存计数

只读检查110现用vLLM源码：entrypoints/openai/cli_args.py 的 enable_prompt_tokens_details 默认False；chat_completion/serving.py 的 _make_prompt_tokens_details 在该值为False时直接返回None。110现用服务命令启用了prefix caching，未包含 --enable-prompt-tokens-details。144本次3次响应的prompt_tokens_details也均为null。

因此缓存API计数缺失与实际缓存命中可并存。下一次获准启动服务时可加入 --enable-prompt-tokens-details；本次没有重启服务或修改部署。结构修复解决可纠正诊断，BTR仍存在模型未遵循游标协议的实测残余问题。
