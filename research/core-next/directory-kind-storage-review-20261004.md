# 信息目录类型独立复验

本轮审查普通 data_list 的目录可发现性修复，范围为 Information 虚拟挂载根、SQLInformation 静态路由目录及 LLM 工具说明。作者修改两处目录项构造并补充说明，未改变实际数据查询、权限、分页或行动预算。非作者未修改产品。

## 一、合同

Information 的虚拟挂载点沿既有 list_files 合同作为命名空间目录，普通 list 现在同样返回 kind=directory。挂载最小 Provider 仍只需既有方法，未增加 stat 调用或新要求。独立用例挂载没有 stat 的 Provider，验证根精确 total、续页及撤销发现权限后拒绝旧 cursor；Provider 自己返回的业务行完整保留。

SQLInformation 的静态路由可确定为容器，统一补 kind；查询返回的真实行不注入类型。独立强例故意存业务字段 kind='directory'：普通 list 与 Observation.query 保留该原值，VFS 通过 list_files 把对应实际记录正确标为 file。目录根只显示获授权子路由，权限集合变化后旧游标拒绝，total 重新计为可见路由数。作者另验证普通目录 kind 与 stat 相符、原文文件读取完整。

首次文案将所有 kind=directory 项泛称为容器，审查指出它可能覆盖业务字段。作者已将说明限定为 Directory entries 的 path/kind，明确 dataset rows 保留自己的字段。实际 Driver 提供方请求测试确认 data_list/read/query 的说明进入工具 schema；没有特定领域路径、列名或预填答案。

## 二、验证

独立两项加作者及 SQLInformation、Interaction、Observation 领域、Shell、Workspace 相关组，共 67 通过，最终日志 [directory-kind-storage-final-green](directory-kind-storage-final-green-20261004.txt)。初次 67 项也通过，最终复跑把业务字段加强为与目录标记同名的 directory 值，并包含修订后的实际工具说明。当前审查范围无未解决问题，git diff --check 通过。

本修复改变产品的公开发现输出，`3c62eb4` 的 671 项离线证据继续属于旧身份。最终源码冻结后须按新提交运行确定性、实验与实际 pilot 渲染；真实网络组由对应执行者按新候选部署，本次小组不替代该验收。
