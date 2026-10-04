# 主体统一文件接口

本轮把实际主体认知、共享原文、当前私有工作区及已登记结果接到同一文件地址。代码、目录和工件仍是当前未提交候选的实施与软件验收证据。

## 一、合同

LLM 顶层信息工具使用 read、ls、find、grep；领域 Actions 与 bash 保持原有预算及执行语义。ActorFiles 绑定 Session，/world 复用 Information 授权与原文范围，/context 保存本次有效系统背景、感知增量、扩展消息及明确实际经历；普通 mounts 提供 list/read/revision 接口，Memory 和第三认知插件通过同一接口接入。首次与同 Moment 再激活使用 InputBatch.context 或既有 Thread.input_context 取得有效背景，不复制全部历史。

/results 使用 Thread actor/reference 唯一索引、规范写者同步维护的结果总数和不可变工件身份；跨激活与完整点恢复按主体继续读取。相同主体重复登记同一 reference 时保持原工件身份，冲突正文明确失败。shell 输出及 data query 回执提供同一 /results 路径。上下文与结果以只读 mount 进入 Bashkit；专用文件工具读取当前 Overlay 修改，冷持久文件从 WorkspaceLease 范围源读取。Overlay 写入水位决定文件版本，纯读取命令保持版本有效。

SQL 数据集提供可读 @schema.json，目录分页保留总数与继续游标；源 document_ref 保留内部 path、Ref 与可直接 read 的 logical_path。普通原文、显式 manifest 与 parts 的同一地址在 shell 和专用工具中具有一致语义。带斜杠、空格、@、百分号及原文字面 %2F 的键在 ls/read/find/grep/bash 中读取同一对象。

find 沿原始文件目录查询，固定源与授权依赖水位，把完整路径结果写到临时 JSONL 后登记工件。标准 BufferedReader 按页读取结果，长路径可跨缓冲边界；分页成本由本页路径字节与缓冲预读决定。grep 对每个原文件单独使用成熟连续 Reader，逐行写 JSONL，预览最多二十条且每行一千字符，完整结果仍可 read。全部 UTF-8 原文段参与标准增量解码验证，二进制请求获得明确失败及 base64 原文通路；源变动、撤权、取消和搜索错误不会登记成功结果。

## 二、验证

actor-files-red.txt 保留 ActorFiles 不存在时的失败先行消费者。actor-files-green.txt 记录调度、ActorFiles、真实 LLM 循环、shell、信息范围、Thread 与恢复等 121 项组合通过；随后独立跨消费者五项通过。actor-files-stateful-green.txt 记录统一文件十二项、Hypothesis 状态机及独立交叉消费者的通过结果，状态机使用真实 StageStore、Bashkit 与标准文件字典 oracle，最多十二组、每组二十步，覆盖写、改、删、重命名、字节范围、ls、版本失效、主体隔离、完整点及恢复。

完整原文试验保留超出 64 KiB 的中文长行、源字节位置和行号；grep JSONL 可在恢复目录继续读取。Event 屏障证明取消时源回调先结束、随后工件关闭，既无成功结果登记也无继续 sink。独立验收还覆盖四十条已物化结果在撤权与恢复后按原主体继续读取，以及短路径一页源字节预算；相关证据由 cross-review-filesystem.md 管理。

本工序已经冻结 ActorFiles、LLM tools/dispatch、shell、Thread 及调度源文件。真实模型提供方调用、完整确定性全量、安装与资源对照由主任务进一步验收。Bashkit 的整读接口及 Overlay 首次新正文范围物化仍保留成熟上游成本，资源报告按实际读取、CPU、峰值内存和临时盘说明边界。
