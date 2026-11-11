# TypeScript 构建记录

Society0 的当前实现位于 `typescript/`，使用真实 Cordis、SQLite 原生 SQL 和 Session，直接复用 pi 的模型与 Agent 循环。公共交付覆盖完整 Core、基础插件与全部 Social。

## 构建与验收

使用 Node 满足包清单的 `engines.node` 要求，以及 Python 3、Git 和 POSIX `patch`。依赖由 `package-lock.json` 固定。

```sh
npm ci --ignore-scripts
npm run build
npm test
python3 tests/provenance.test.py
python3 scripts/acceptance.py /absolute/path/outside-repository/new-evidence-directory
```

构建输出在 `.build/<package>`，发布身份在 `.build/release.json`。验收脚本在仓库外的新目录生成 tarball，逐包离线安装两个 TS 插件和一个 JS+JSDoc 插件，执行严格类型检查与运行测试。输出目录必须在仓库外，并保留安装锁文件和打包文件清单。

## 已经验证的工程决策

- Cordis 采用固定来源的真实内核，并成套修正 binding 承诺、结构 child 排空、异常清理和 Loader label 转移。行为检查覆盖这些边界；生产包仍要独立安装和关闭。
- Vendor 按其原有动态代码边界单独构建声明；第一方宿主和第三方 TypeScript 消费者保持严格检查。补丁没有新增原始 vendor 的类型诊断。
- SQLite 流式回调可能包含整个大行；Session 捕获中断阈值不等于硬内存上限。存储实现需要组合内联行约束、正文对象引用、分段 Session 和原生预算。
- SQLite 硬内存限额作用于同进程同库实例的全部连接；OOM 后可能需要重建 Session。失败事务和不健康 Session 不得推进完整点。
- 构建重新应用有序补丁并逐文件比较，然后机械重映射发布 scope 和导入名。上游许可证逐包保留；每个 tarball 带来源、构建依赖锁和补丁原文，便于仓外诊断。
- vendor 的独立编译允许来源中既有的 implicit any/this；host、CLI、两个 TS 插件和 JSDoc 插件保持完整 strict，消费者使用 `skipLibCheck=false` 验证真实声明边界。

## 验收入口

`npm test` 执行包清单中 `scripts.test` 指定的 PR01、PR02 测试。构建、来源和插件打包消费分别由上面的命令执行；完整公开 SDK 用户链使用 `tests/pr12-consumer.mjs`、`tests/pr12-four-roots.mjs` 和 `tests/pr12-model-branch.mjs`，参数指向独立安装目录和新证据目录。

宿主基础验收见 `tests/consumer.test.mjs` 及 `tests/provenance.test.py`。原生生命周期测试和 Loader 行为测试直接消费安装后的包。每次关闭都要等待返回的 Promise 完成，再报告资源已回收。
