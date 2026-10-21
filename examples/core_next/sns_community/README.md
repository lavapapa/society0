# SNS 社区实验

六位使用网名的用户讨论“收藏夹要收费”的截图传闻。平台提供发帖、评论、点赞、转发、关注与推荐，用户每轮自行选择一次社交动作，长期记忆在轮次之间保留经历。

本目录的配置和脚本对应 2026-10-19 的三轮试运行。真实结果已整理为 [单文件工作台](../../../docs/examples/sns-workbench.html)，下载后可离线打开；[WorkBuddy 图文教程](../../../docs/workbuddy-next-walkthrough.md) 使用同一案例。`workbench-data.json` 包含这次已保存的逐轮快照与会话，可配合仓库的渲染脚本重新生成 HTML。

## 一、配置

从仓库安装 `llm`、`memory` 和 `social` 依赖组，将 `config.py` 和 `experiment.py` 复制到自己的实验版本目录。通过 `SOCIETY0_LLM_BASE_URL`、`SOCIETY0_LLM_API_KEY`、`SOCIETY0_EMBED_BASE_URL`、`SOCIETY0_EMBED_API_KEY` 提供服务信息，模型名称与嵌入维度见 `config.py`。本例启用长期记忆，所以需要嵌入服务。

在版本目录中执行：

```sh
python experiment.py --check --run-dir runs/check-001
python experiment.py --run-dir runs/pilot-001
```

每次尝试使用新的运行目录。动作入口来源会列出真实帖子、用户引用和调用格式，与平台推荐流独立；模型仍按人设和当轮信息选择动作。

## 二、结果

v014/pilot-001 完成三轮、18 次用户激活。最终保存 2 条帖子（1 条初始投放、1 条用户新帖）、17 条评论、0 个赞、5 条初始关注边和 41 条长期记忆。本次没有转发或新增关注。完整用量为 40 次聊天请求、93,512 输入 tokens、4,788 输出 tokens，以及 19 次嵌入请求、2,517 输入 tokens。

[结果记录](results.md)提供会话定位、记忆召回数量及可进一步比较的条件。

v013 关闭动作入口的尝试在第一轮因错误对象引用耗尽回合，保留在工作台版本列表中。v014 启用入口后完成运行。一次成功试验支持检查配置与流程；评论表达相近及动作分布的原因，仍需对照人设、任务提示、入口与模型等条件研究。

## 三、工作台

已有数据可直接渲染：

```sh
python skill/scripts/render_workbench.py \
  --data examples/core_next/sns_community/workbench-data.json \
  --output workbench.html
```

本例保留上述静态数据的渲染方式。新实验从 [society0 主流程](../../../skill/SKILL.md) 开始，并按 [工作台交接](../../../skill/references/workbench-guide.md) 加载独立的 society0-workbench Skill；展示和用户选择按它的实际接口处理。

阅读时先选版本、运行和轮次，再从关系网络、帖子详情追查用户会话。推荐动态与动作入口属于不同信息来源，应分别核对。
