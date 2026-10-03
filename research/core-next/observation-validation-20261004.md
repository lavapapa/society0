# 外部观察验收记录

本组在 `society0-core-next` 工作树、567fc8f 基线之后完成候选。新增 observation.py，领域插件的变动限于将运行时已经使用的 SQL 资料路由抽为共用只读工厂。Thread publish_step 与可复用 StageReader 已在先前 537ac50 固定。本文记录候选实测，发行部署仍以最终提交为准。

## 一、正确性

先保存模块缺失、CLI 生命周期、稳定派生视图等红灯，再实现相应能力。作者纵向覆盖独立 producer 进程的完成前缀、未完成失败原文、持续目录与 tail、跨 fork 身份、巨消息/资源/结果/binary 工件分段、真实 Social/RoundRobin 权限路由及零副作用；HTTP 覆盖慢 body 的槽满 503、断连释放与暂停大响应读取时另一状态请求可服务。

历史准备为单独进程，测试准备忙、kill、保留旧 ready、clear 回收、本机退出及关闭后拒绝新分配。非作者 review 找到公开 prepare_complete 在服务关闭后仍能启动进程；共享检查已移入实际分配入口并在锁内执行，红灯为 observation-independent-closed-red-20261004.txt，非作者复验记录为 observation-storage-independent-green-20261004.txt。

稳定只读身份经 StageStore.restore 的 run_id 参数传入。真实 CLI 多进程读取同一个完整点，目录下一页与巨正文后续字节段保持可读；换完整步骤会拒绝旧游标。清理后的 HTTP view 明确失效，相同完整点重新准备完成后恢复该派生身份。每次 CLI 历史调用都会实际重建副本，连续消费宜复用服务。

最终直接组合覆盖 Q、领域、Results、Reader 与 Thread 发布归属，67 项通过，详见 observation-final-direct-green-20261004.txt。最初组合命令引用了不存在的历史 review 文件，未执行测试，其命令错误保留在 observation-final-command-error-20261004.txt；修正文件清单后重新完成上述组合。候选的后续非作者增量复验单独记录，不把作者重跑计为新的独立审查。

## 二、成本

本机实际 HTTP 100 次 status 请求，20 张表创建 100 个只读连接，总墙钟 52.3 毫秒、p95 0.672 毫秒；1,000 张表仍为 100 个连接，总墙钟 143.8 毫秒、p95 1.695 毫秒。长期 Python `with Observation` 各使用一个连接，1,000 表同样 100 次请求约 8.39 毫秒、p95 0.096 毫秒。脚本 benchmarks/core_next_observation_probe.py，结果 observation-http-cost-20261004.json。各档是一次本机探针，计时包含本机调度和 HTTP 客户端，不能外推到共享文件系统或慢盘。

当前 Thread 完整前缀采用 `(thread_id,publish_step,seq)` 索引降序定位，100 与 10,000 个历史事件的固定末尾请求原生 VM 指令量小于二倍，测试同时禁止通过全正文读取取边界。目录把所需短列一次查询，不再逐条重查 head。依赖表版本的保守失效也有真实消费者：每次续页之前另一主体更新同表，20 次续页全部明确过期；停止相关表更新后查询完成。这暴露了高写入率下固定页可能饥饿的边界，不把轮询当作无损事件流。

正文预算按最终 JSON 验证，包含中文、引号换行转义和 base64 膨胀。wire 预算没有升级为对任意插件提供者内存的保证；SQL 巨字段预检仍为独立 P04 项。原生 DocumentSpec、Thread/resource chunks、Results range 都按请求范围读，完整正文测试逐字节或逐值比较。

## 三、范围

完整视图准备仍执行真正恢复与副本创建。换视图期间可同时存在旧 ready 与新 building，每个恢复目录含 current、root 和声明工件。准备状态给出的 logical_bytes 是完成目录大小；源、WAL、旧 ready、building 的实际峰值需结合 V04 大根另测，当前小型纵向不宣称解决了大库恢复成本。

资源调用原文与逻辑/物理身份已经可以有界读取，按主体或模型的实时累计用量投影保留为 T06 后续消费者。工作台、完整 runner manifest 和最终真实提供方全链仍在相应后续任务中。上述边界保留在合同与 TODO 中，本次候选不会借小探针通过替代整项目验收。
