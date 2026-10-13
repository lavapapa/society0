# 基础组合验证记录

本文保存本任务实际执行证据。完整 E2E、成本和最终全量结果尚待对应任务执行。

## 一、环境

解释器与基线见[实施记录](implementation-plan.md)。测试使用任务工作树的 src，清除八项 HTTP/HTTPS/ALL/NO_PROXY 大小写变量，SOCIETY0_RUN_REAL_E2E=0、SOCIETY0_RUN_CORE_REAL=0，pytest 排除 real_e2e。测试期间工作树允许本任务未提交差异。没有真实端点调用。

## 二、T01

失败先行：执行 `python -m pytest tests/primary/test_registration_ownership.py -m 'not real_e2e'`，退出 1，共 10 个失败。原因包括旧 mount/register 返回 None、便捷 register 缺少 on_close 参数和部分安装时已登记项无法独立撤销，未出现测试收集或缺依赖失败。原记录：[t01-red.log](evidence/t01-red.log)。

实现后相同文件退出 0，10 passed；见[t01-green.log](evidence/t01-green.log)。覆盖独立注销、同对象重挂载、旧 handle、owned/borrowed、FTS/tags、共享依赖引用、generation 与游标、部分安装失败、作用域错误/取消，以及真实 RR/Social register 的中途错误资源托管。

相关回归：registration_ownership、kernel_interaction、kernel_interaction_review、kernel_composable、plugin_round_robin、plugin_social、social_composition、kernel_domain_revisions、kernel_record_plugin、skill_starter、kernel_memory、core_next_pilot，共 159 passed，退出 0。见[t01-focused.log](evidence/t01-focused.log)及[t01-focused.xml](evidence/t01-focused.xml)。完整命令可由上述文件列表组成 `python -m pytest ... -m 'not real_e2e'`，PYTHONPATH 取任务 src 和根目录。

## 三、范围

T01 结果证明当前共享登记和安装所有权行为。两阶段停止、完整公开配方、组合多来源认知以及独立跨进程 E2E 尚未实施，不计入当前通过。历史 SQL 来源缓存观察仍是前轮证据，推荐修复将在 T05 重新取得失败和通过结果。
