# 调度实施

SequenceSchedule 冻结完整 times 与 phases，按 completed_step 直接定位下一项；重复规划读取返回同一计划，无可变游标。StepPlan 将时间与阶段传给 Runtime，Schedule 协议无需 runtime/phases 字段。schedule_plugin 独立安装，RunPlan 分别注入 schedule 与 runtime 服务并删除 moments。

第三方调度消费者使用正式 RunPlan/run_plan、三时点完整线和所选完整点恢复，证明没有绑定内部字段；单阶段、多阶段、失败步骤恢复继续保留先前完整事实，恢复时消费者传入全线。公开示例、工作台、教程测试与真实模型支撑工厂已迁移；真实端点测试恢复参数改为完整线。

失败先行：新协议测试因 StepPlan 尚不存在而收集失败，证据 schedule-red.txt。实现后相关调度、runner、工作台、教程共 31 项通过，证据 schedule-green.txt。测试检查重复读取、时间序列身份、失败发布边界、完整点恢复、第三方服务选择、计时与诊断失败隔离；尚未在本工序运行真实模型端点。

规划定位为 O(1)，初始化物化时间线为 O(时点数)，每次步骤阶段工作量由本次阶段及活动主体决定；runner 不扫描累计历史。冻结完整时间线仍由公开 RunContract.time 记录实验范围，runner 的运行容量取自显式 Runtime。
