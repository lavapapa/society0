# 固定旧版环境对照

本报告记录实际旧引擎与 Next 工作树的确定性对照。代码限定为独立录制脚本和实验测试；业务源码未由本任务修改。共同轨迹见[逐笔记录](<oracle-common-trace.json>)，运行身份、依赖和计数见[数字工件](<oracle-evidence.json>)。

## 一、来源

旧源固定为 `v4.1.14 / 4ff2df74668931aef12b1a4951fab1d97d6a0981`，由本地 Git 导出完整 `src`，三个环境文件与 `git show` 原字节核对。每次录制使用新的子进程、源码目录和运行目录，核对实际加载的包路径。旧脚本导入并执行真实 `Society0`、PlainEnvironment、RoundRobinConversationEnv、SocialNetworkEnv、Agent 和 ActionSet；没有替换旧方法或复刻旧算法。

Next 基础提交为 `7320b13a7d8d58cbe116f9bbc407224fec05de3c`，执行本轮并行修改中的工作树。旧新共用原仓 Python 3.12 虚拟环境，子进程使用各自的 `PYTHONPATH`。已有依赖足以运行这些无模型场景，无额外安装、外网模型或付费请求。旧依赖声明中的 ollama、jmespath 未安装，这些脚本未触发相应消费者；实际依赖版本保存在数字工件中。

入口为[旧引擎录制](<../../../tests/reference/basic_env_v4114_oracle.py>)、[隔离与比较协议](<../../../tests/reference/basic_env_oracle_protocol.py>)、[Next plain/RR 适配](<../../../tests/reference/basic_env_next_oracle.py>)、[Next social 适配](<../../../tests/reference/basic_env_next_social.py>)与[验收测试](<../../../tests/experiments/test_basic_env_oracle.py>)。

## 二、结果

焦点验收完成 23 项：旧独立进程重复确定性、完整点恢复、plain/RR/social 旧新共同语义、Next 新进程恢复继续，以及主动损坏比较器测试。连同独立审查的认知与 oracle 探针，合计 30 项通过（6.92 秒；此时间包含测试启动与录制开销）。

| 场景 | 实际执行与比较 |
|---|---|
| plain | 旧 Society0 三 tick 空环境控制；Next RunPlan 四规则主体三 tick，另从完整点 1 新进程继续。比较主体目录顺序、tick 和空领域状态。旧 plain 未激活规则行为，因此此项不作为驱动成本等价证据。 |
| RR | 四人三轮共六对、12 条私信与三次广播展开的九条收件消息，逐条比较全部 21 条正文、收发人、轮次和顺序；比较每轮收件箱、伙伴历史、参与标记、小组成员、总轮次、建议时长、当前及未来计划。动作返回的状态、收件人、message_id 与广播投递序列也比较。Next 经 Actions 与 Information/原文入口消费，旧 FoV 原文另留录制。四个主体均含非空 persona、opinion、confidence；逐轮及恢复后比较持久化 actor record，此项尚未证明这些资料全部进入 LLM 感知。 |
| social | 18 次动作包含固定有向图的四次关注、发帖、四次点赞、评论、转发、取关和三次热门行动；另执行一次确定性标记干预。每次动作后比较帖子原文、作者、引用、标签、点赞顺序、评论原文及时点、所有计数、图边和逐条通知载荷。最终五帖、十通知。 |
| 推荐与曝光 | 三步逐候选身份、完整评分分量、最终排名顺序和热门次序精确相等；两种预览均不增加曝光。每步 feed 加热门 action 的待提交曝光与 after_tick 后累计浏览数对照。最终 post_1…post_5 浏览数分别为 6、3、0、1、1。 |
| 热门反例 | keep_count=1；tick0 活动池为 post_1/post_3，热门前二仍为 post_1/post_2。三个时点的热门前二保持一致。 |

旧连续结果与 Next 连续结果在共同协议上相等。Next RR、social 从完整点 1 在另一进程继续，结果同样与旧连续结果相等。Next plain 使用正式 RunPlan；RR/social 使用 compose、真实交互服务与显式完整步骤，social 显式调用实际 after_tick。后两项覆盖数据与机制恢复，Runtime 自动阶段编排仍需集成测试补充。

比较器对字典字段、值类型、列表长度、每项内容与顺序严格检查，输出第一处差异路径。原始录制损坏单条消息、单次曝光、一个伙伴、一条通知或热门次序均会失败；共同协议中再分别损坏消息、配对、曝光，也均会失败。动作返回共同协议另行映射实际成功/失败、关注目标、点赞 changed、发帖/转发 post_id，以及热门返回正文、作者与计数。旧返回未知或失败文本保留诊断；Next 显式保留 result_status，评论返回 reply_id 必须关联刚创建的实际评论。独立审查发现的“只损坏 action.result 却被漏过”已补为失败用例；追加旧返回失败/错误 id/正文/作者/浏览数与 Next 返回状态/changed/id/正文损坏检查。没有把这些故障标成跳过或预期失败。

## 三、差异

旧 social 完整点恢复存在可复现缺陷：从四条动态关注边的完整点 1 恢复后，下一次发帖后的图为零边；第一处比较失败是 `$.actions[13].state.edges: length 4 != 0`。随后 d 取关 a 的旧连续结果是 `Successfully unfollowed a`，旧恢复结果是 `Not following a`。旧 initialize 会重新生成配置中的空图。测试精确保留此差异，同时要求旧 plain/RR 恢复等价；Next social 恢复继续按旧连续事实验收。

标准化范围明确：RR 消息墙钟 timestamp；旧随机 reply UUID 的一一顺序别名；通知与评论的物理存储 id；API 返回排版。Next 通知新增 reply_id/repost_id 导航字段单独保留于原始输出，共同比较保留旧载荷中的所有字段。旧候选池是集合，比较其成员；最终推荐及热门排序维持严格次序与精确浮点值。通知消费状态、旧专有 votes/embedding 元数据不在本共同协议内。

本轮没有覆盖语义向量/预录向量、五类拓扑生成、完整 LLMDriver+scripted provider、异常动作矩阵、大规模分页、通知消费及环境成本分解。旧 FoV 与 Next 信息路由比较研究信息内容，未要求逐字符排版一致。全仓离线回归与非作者审查由集成代理执行；本报告不作性能收益或发布结论。

## 四、复现

在本工作树执行以下命令，测试会从本地 Git 自动导出固定旧源码并运行两侧隔离进程：

```sh
env -u ALL_PROXY -u HTTP_PROXY -u HTTPS_PROXY -u NO_PROXY \
    -u all_proxy -u http_proxy -u https_proxy -u no_proxy \
    PYTHONPATH=src \
    '/Users/marvin/Documents/同花顺（2）/research/simulation/society0core/.venv/bin/python' \
    -m pytest tests/experiments/test_basic_env_oracle.py -o addopts= -q
```

定点重放已知旧恢复缺陷可加 `-k legacy_complete_point`；该测试同时断言旧缺陷的精确观察和比较器拒绝，预期测试通过。原始运行目录由数字工件的 `run_root` 指向，可读取旧完整源、真实检查点、旧输出及 Next 输出。临时路径存续受本机清理策略影响，永久留存的共同轨迹与脚本支持重新生成。

这些证据支持固定脚本的逐笔等价与 Next 恢复判断，后续验收应围绕上述剩余范围继续扩展。
