# 文件底座独立交叉审查

本审查由认知扩展实现者执行，审查对象为另一实现者负责的原生搜索、InformationFiles、SQLInformation、Workspace 与 social 只读迁移。审查以文件交互规格中原文身份、范围成本、选中范围水位、取消收束与真实业务信息完整性为标准。

## 一、发现与修复

第三方信息提供者可以只有目录节点 stat revision，甚至返回 None。原 Information.search_revision 将该值作为整个所选子树的稳定身份使用，因而无法证明遍历期间子文件与授权条件保持同一水位。新增三个用例先失败，再删除 stat fallback；选择参与搜索的插件必须明确提供覆盖所选范围的 search_revision，缺少该方法或返回 None 时明确报错。该修复没有增加扫描、哈希或全局注册机制。

注册数据集已经有 metadata 服务，但 InformationFiles 未暴露文件入口，空数据集尤其无法让主体从文件视图获得字段合同。新增空数据集 @schema.json 消费用例先失败，再增加目录发现、读取、stat 与 exists 投影。缺少 metadata 能力由 Information 以 Unavailable 明确表达，提供者内部 AttributeError 继续暴露。ActorFiles 的相应公开路径由另一实现者接入。

## 二、独立用例

新增 test_filesystem_cross_review.py 共九项测试，覆盖第三提供者缺少完整范围水位、空数据集 schema 文件、包含斜杠与百分号及中文和保留标记的实际键在内部原文路径与 shell 投影中保持同一身份、交错主体读取导致旧 SQL spool 关闭、撤权与 scope 关闭后缓存无法提供新读取、native sink 取消后回调在返回前收束。

双挂载测试验证搜索开始冻结全部已选择挂载：读取第一个文件时改变第二个挂载，整个 /world 搜索失败且不登记成功工件；相同变化发生在选择范围之外时，对 /world/first 的搜索仍可完成。该测试同时检查版本失败边界与避免不相关挂载造成过度失效。

新增九项通过。另组合运行 native_stream_search、filesystem_ranges 与 plugin_social，40 项通过，覆盖 UTF-8 跨回调块、超长行、独立文档边界、Reader 取消、文档范围、workspace 真实 artifact 范围，以及 social 完整帖子、评论、点赞事实、profile 与恢复后的原文读取。SQL spool 是单槽近期原文缓存；主体交错会重新物化同一条记录，容量保持一个临时文件。完整 social post_details 会按其实际内容读取相关评论和点赞，范围续读复用已物化 spool，不把首次完整 JSON 物化描述为常数工作量。

## 三、边界

原生搜索采用 ripgrep 的连续 Reader 与逐行 matcher，没有自定义块重叠算法；行缓存工作量仍随最长行增长。取消收束检查覆盖 Reader 与 Sink 两条等待路径，测试不推断极端原生 CPU 计算可立即抢占。Bashkit 整文件 read_file 合同及 Overlay 上层首次完整物化属于现有明确成本，专用冷 workspace 范围读取已经独立验证实际 artifact offset/size。

源码审查确认 social post_details 水位包含帖子正文、点赞、评论与标签依赖；profile 水位包含公开主观资料、发帖与关注关系依赖。原 social 推荐与曝光写入行动保留。正式整体全量、构建矩阵与真实端点验证由整合工序汇总，本报告不将离线通过升级为长时研究结果。
