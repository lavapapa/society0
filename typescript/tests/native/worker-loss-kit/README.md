# Worker-loss定向回归

三个源码文件与已消费的便携夹具逐字节相同。入口文件使用普通mjs名称，保留独立定向执行；仓库默认测试脚本保持原样。

## 一、环境

Node >=24.16.0，安装同一候选发布包的独立consumer，包括公开Host/Core及其原生SQLite传递依赖。将本目录整体复制到consumer根目录下，模块按consumer自身node_modules解析。使用其已经验证可加载的原生构建及系统依赖。

## 二、执行

在consumer根目录运行：

```sh
ulimit -c 0
env -u PR04_FAULT_PHASE -u PR04_FAULT_OPERATION \
  SOCIETY0_FAULT_OUTPUT="$PWD/worker-loss-results" \
  node --test --test-concurrency=1 worker-loss-kit/cases/public-worker-fault.mjs
```

[定向入口](<cases/public-worker-fault.mjs>)自行安装和恢复故障hook，无需预载。[支持夹具](<cases/public-fault-support.mjs>)使用公开Host、execution-fence、SQLite入口。数据库保留在TMPDIR下；结果与事件写入指定输出目录。新一轮证据使用新的输出目录。

## 三、边界

覆盖BEGIN前终止、长递归SQL派发20ms后实际Worker.terminate，以及截断成功COMMIT回复后终止。长SQL阶段覆盖实际派发，区别于等待execute完成后终止空闲Worker。事件记录描述RPC派发/回复与真实退出，原生程序计数器位置保持未采样。

三阶段断言callback次数0/1/1、not-committed/not-committed/committed receipt、COMMIT未知错误、halted、在途字节与acceptedCommits归零。[故障模块](<worker-fault.mjs>)也支持另一个消费者通过node --import预载；使用PR04_FAULT_PHASE与实际唯一PR04_FAULT_OPERATION选择Worker。独立预载与三阶段入口分别使用。
