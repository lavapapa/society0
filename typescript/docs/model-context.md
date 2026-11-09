# 模型上下文窗口取值

`profiles/social-sqlite.json` 中 `Qwen3.6-35B-A3B-FP8`（`provider=local-gateway`，`baseUrl=http://127.0.0.1:4000/v1`）的 `contextWindow` 取实际服务的 `max_input_tokens`，不是旧值 8192。

## 核对记录

- 日期：2026-11-09
- 只读接口：
  - `GET http://127.0.0.1:4000/v1/models`：该模型 `max_input_tokens=262144`、`max_output_tokens=262144`
  - `GET http://127.0.0.1:4000/model/info`：`model_info.max_input_tokens=262144`、`max_output_tokens=262144`
- 结论：`contextWindow=262144`
- 未改 LiteLLM / vLLM 配置，未重启服务。请求使用的网关凭据只从进程环境读取到内存，未写入本文件或日志。

pi-ai 1.3.0 在 `dist/api/simple-options.js` 固定预留 `CONTEXT_SAFETY_TOKENS=4096`。按 262144 设置后，4.2k 估算 prompt 仍有充足输出空间。运行时另有本地校验：若 pi-ai 实际给出的 `max_tokens` 低于 driver budget 的 `output` 槽，则该次激活以 `BUDGET_OUTPUT_TOO_SMALL` incomplete 结束、不发物理请求。
