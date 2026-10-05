# 社交浏览失败的真实交互诊断

`remaining-live-01` 在该例之前九项通过，本例主体 b 达到原有八轮上限。以下依据真实 Thread，全文副本为 `social-browse-failure.json`。未调用提供方、未修改目标、预算、提示词或产品。

## 一、序列

主体 a 的目标明确要求发现并执行 get_trending_posts。第一轮尝试未带命名空间的 action_describe 得到 Unavailable；第二轮 action_find 得到完整名 social.get_trending_posts；第三轮 describe 得到空对象参数；第四轮以原生对象 arguments={} 成功执行。返回帖文完整包含“重要市场信息：明日物流费用增加10%。”。a 随后完成自动记忆提取，其 Thread 为 completed。

主体 b 的目标是“请回答已发布市场信息”。其第一轮在给定 memory actor 引用上列动作，第二轮描述 memory.recall，第三轮用“已发布市场信息”召回，第四轮改“市场”召回；两次均成功返回空 memories。第五轮 data_list('/') 发现 social 目录，第六轮列 /social 得到八个明确目录，第七轮列 /social/posts 得到 post_1 元数据，第八轮 data_read('/social/posts/post_1') 成功返回相同元数据的 JSON。此时既无正文答案，也无原始市场正文，Thread 为 incomplete/max_turns。

## 二、判断

b 的八轮均无参数类型或 SDK 工具协议错误，原生对象调用正常。前四轮围绕空个人记忆搜索是实际模型策略；随后目录发现与结构记录读取都成功。最后读取的帖子记录只有 id、作者、时间、计数和指回该记录自身的 ref，缺少其独立正文入口。这里存在可直接观察的信息关联缺口：主体发现一条业务实体后仍须从另一目录推测该实体与正文的连接关系。

源码 `_social_routes` 将 posts 指向 social_posts，将 content 指向 social_bodies，二者通过 id 关联，但 posts 行未传递这一关系。`SocialInformation.query` 对 feed 已提供 content_path，普通 posts 与离线 `social_information` 均没有同等链接。因此冷热拆分内部结构对普通主体阅读形成额外知识要求，当前目录可见性本身未表达“该帖正文在哪里”。

## 三、最小范围

可验证的产品改进是让帖子记录声明可继续读取的正文引用，并由相同声明服务普通 list/query、记录 read、只读观察和 VFS；保留元数据、权限、版本、分页及范围读取合同。回复与通知也采用独立正文表，若处理共通关系，应由已有注册投影声明关系，避免在模型提示或客户端硬编码 social 路径。此处仅提出范围，未写代码。

该改进使信息连接明确，却不能保证本次模型在八轮内完成：本次直到最后一轮才读取记录，即使第七轮获得链接，第八轮读到正文也尚需自然答复轮次。模型前四轮的选择与给定预算仍属于真实可用性结果，不能通过多给一轮或强制结束将其改判成功。现有 evidence 支持修复正文可达关系，随后按原目标与预算重新验收；当前失败保持失败。
