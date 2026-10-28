# PR01 生产验收

以固定 DSH 六包源码 cohort 编译独立 ESM JS 与声明。原始 vendor 保留来源和许可证；生命周期补丁按 patches/series.json 顺序应用；分发时机械映射到 @society0 scope。宿主只挂载显式 boot，提供真实 Context/Service。所有包预检完成、所有入口归一化成功后才调用原生 ctx.plugin。

验收：空 boot 无模型/数据库；两个 TS 与一个 JSDoc 插件使用仓外 tarball、公开 host 类型增强及 native required 注入；逐一卸载贡献与最终资源清零；歧义 default/apply 在任何 apply 前失败；缺 exports/目标在任何插件 import 前失败；重复 framework/peer 冲突给出物理路径；生产包无源码 hook/工作区 alias；来源/补丁清单验证失败阻止打包。

PR03 的 profile 规划、动态插件治理和完整运行就绪审计另行实施；PR01 验证 boot Fiber 激活，原生动态 API 保持可用。
