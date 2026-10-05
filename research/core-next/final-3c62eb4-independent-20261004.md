# 最终候选离线验证

本轮对已推送且远端分支相同的 `3c62eb473b59dd3c3ffabb4ab5b1816a3afbdc9d` 使用 git archive 建立独立目录 `/tmp/society0-final-3c62eb4-storage-20261004`。归档的 src 与 native 共 50 个文件逐字等于该提交，测试与 fixture 从同一归档读取。没有模型网络调用、产品修改或依赖锁修改。

## 一、结果

主确定性组为 671 通过，15 项实际 real_e2e 显式排除，用时 32.00 s。全部 experiments 为 72 通过、1 跳过，用时 3.00 s；唯一跳过是正式依赖没有 sqlite-vec 的隔离候选模块。随后借现有独立实验环境的 sqlite-vec 路径复跑该模块，3 通过、0 跳过，用时 0.42 s。此路径没有安装或改动正式依赖。

正式 conversation_pilot 经公开 runner 完成两步，诊断错误为零；workbench CLI 从该次实际工件导出 payload，默认 npm test 运行真实表格与指标曲线消费者，9 通过、0 跳过。Node 默认串行设置生效。上述六个执行命令全部退出 0，精确命令、墙钟、原始日志路径、依赖版本及身份记录在 [机器结果](final-3c62eb4-independent-results-20261004.json)。可复跑编排脚本为 [独立 runner](final-3c62eb4-independent-runner-20261004.py)，它固定该提交并要求新的目标目录。

## 二、范围

源码来自独立归档，Python 使用现有本机锁定环境，Node 复用已安装 node_modules；因此本轮是 macOS 确定性与实际消费者验收，Linux 干净构建及真实提供方 15 项另有执行者。本轮不把 fixture 提供方的成功解释为真实网络通过。

工具说明、CognitiveInput 通用环境说明及 V03 完整原文比较器修复都已包含在本提交测试范围。当前离线组无失败，无未解释跳过。最终文档提交若改变产品，按实际差异重验；若仅文档变化，可直接逐字核对产品与测试身份后引用本组结果。
