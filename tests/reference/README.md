# 固定算法参考

本目录保留新版机制比较所需的最小算法片段。来源提交为 `96b1f3b11aee3c146f5b43e0b8158ec29294e98a`，抽取时函数定义、正文与原注释逐字保留；类壳与导入仅服务测试装配。当前有效社会网络配置模型提供测试参数，原算法独立保存。完整旧实现仍由该 Git 提交保存。

比较范围是轮转配对顺序、图生成节点及边顺序、活动帖子池及逐项推荐得分、偏好原文。此参考资产不承担旧 World 或旧运行入口兼容。

| 参考方法 | 原始位置 |
| --- | --- |
| `SocialNetworkEnv._generate_topology` | `src/society0/env/social_network/env.py:704` |
| `SocialNetworkEnv._create_traditional_graph` | `src/society0/env/social_network/env.py:717` |
| `SocialNetworkEnv._build_active_pool_ids` | `src/society0/env/social_network/env.py:1139` |
| `SocialNetworkEnv._score_posts` | `src/society0/env/social_network/env.py:1470` |
| `SocialNetworkEnv._collect_recent_interactions_for_agent` | `src/society0/env/social_network/env.py:1572` |
| `SocialNetworkEnv._build_agent_preference_text` | `src/society0/env/social_network/env.py:1603` |
| `SocialNetworkEnv._create_cv_targeted_graph` | `src/society0/env/social_network/env.py:3002` |
| `SocialNetworkEnv._create_base_graph_for_cv` | `src/society0/env/social_network/env.py:3027` |
| `SocialNetworkEnv._get_base_distribution` | `src/society0/env/social_network/env.py:3037` |
| `SocialNetworkEnv._optimize_cv_distribution` | `src/society0/env/social_network/env.py:3053` |
| `SocialNetworkEnv._increase_mutual_connections` | `src/society0/env/social_network/env.py:3089` |
| `SocialNetworkEnv._decrease_mutual_connections` | `src/society0/env/social_network/env.py:3114` |
| `SocialNetworkEnv._calculate_cv_for_node` | `src/society0/env/social_network/env.py:3141` |
| `SocialNetworkEnv._calculate_average_cv_for_graph` | `src/society0/env/social_network/env.py:3159` |
| `RoundRobinConversationEnv._build_round_robin_schedule` | `src/society0/env/round_robin/env.py:384` |
