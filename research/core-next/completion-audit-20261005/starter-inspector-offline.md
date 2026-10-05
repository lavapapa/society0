# 真实调用前检查器验证

在读取凭据与真实调用前，直接使用 tests.primary.test_skill_starter.load_starter 与 resources 现成确定性消费者，经正式run_plan执行完整两轮，结果位于 /tmp/society0-starter-inspector-offline-20261005。starter-runner.inspect_run 在该结果上通过完成步、唯一权威查看事实、两轮completed、1–7整数可信度及非空理由、alice第一轮ready经验、向量维度、记忆作业、完整原消息与第二轮实际召回原文检查。确定性fixture的向量维度为2，只有该次检查显式expected_dimension=2；真实运行保持1024。

输出可信度3、理由“当前信息尚无官方确认”，经验正文“我读到尚无官方确认的地铁消息”。该记录验证检查器的SQL与Results/Thread结构，不作为外部模型成功证据。
