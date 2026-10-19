"""SNS 教程实验：六位用户、三轮互动，配置中保留所用模型名称。"""

MODEL = {"llm": "gpt-5.4-nano", "embedding": "text-embedding-3-small", "embedding_dimensions": 1536}
STUDY = {
    "title": "SNS 社区：平台改版传闻",
    "question": "一条未经证实的平台改版消息，在六位用户的关注网络里会引出怎样的发帖、评论、点赞与关注行为，以及他们是否会引用上一轮的记忆？",
}
ROUNDS = (1, 2, 3)
SEED_POST = {
    "id": "post-1", "author": "lin", "tick": 1,
    "content": "刷到一张截图，说下周开始收藏夹要收费了？还没找到官方公告，有人知道原始出处吗？",
}
EDGES = (("bo", "lin"), ("chen", "lin"), ("du", "lin"), ("eve", "bo"), ("fang", "chen"))
AGENTS = (
    {"id": "lin", "name": "凌晨两点半", "group": "用户",
     "persona": "经常深夜刷动态，喜欢分享刚看到的新消息。遇到截图传闻会注明尚未证实，也想听听评论区有没有人知道出处。"},
    {"id": "bo", "name": "今天也想摸鱼", "group": "用户",
     "persona": "利用通勤和工作间隙刷社区，收藏了很多教程。很在意产品改版是否影响已有收藏，习惯用简短评论表达担忧。"},
    {"id": "chen", "name": "像素汽水", "group": "用户",
     "persona": "关注数码与产品更新，看到截图会留意日期和出处，愿意追问原始链接。缺少依据时会保留判断。"},
    {"id": "du", "name": "橘子海.exe", "group": "用户",
     "persona": "喜欢摄影、游戏与网络梗，刷到有意思的讨论会参与几句。对技术细节耐心有限，更关心改版后是否方便使用。"},
    {"id": "eve", "name": "不加糖的拿铁", "group": "用户",
     "persona": "用收藏夹整理穿搭与做饭资料，经常看关注账号的动态。会结合自己的使用经历判断消息，也愿意补充具体问题。"},
    {"id": "fang", "name": "一只路过的猫", "group": "用户",
     "persona": "常在评论区潜水，偶尔询问细节。对热门结论保持好奇，会关注讨论中提供有用信息的账号。"},
)
SOCIAL_CONFIG = {"is_directed": True, "social_media": {
    "recommendation": {"post_count": 5, "use_embedding_similarity": False, "feed_content_preview_chars": 280},
    "trending": {"enabled": False}, "content_length_limit": 280,
}}
MEMORY = {
    "auto_write": True, "auto_recall": True, "active_tools": True, "recall_top_k": 3,
    "recall_query": "我此前在这个 SNS 社区看过、说过、赞过和判断过的内容",
}
PLUGINS = {"social": {"enabled": True}, "social_source": {"enabled": True}, "social_entries": {"enabled": True}}
DECISION = {"max_turns": 10, "max_action_calls": 8, "required_tags": ["social_write"], "completion_tags": ["social_write"]}
RUNTIME = {"capacity": 2, "max_activations": 4096}
ENVIRONMENT_TEXT = (
    "这是一个使用昵称交流的 SNS 社区。平台会按发布时间、互动情况、内容相似度和你的关注关系，"
    "从全部帖子中挑出一批推荐给你，每次最多五条；推荐里既可能有你关注的人发的内容，"
    "也可能有你没关注的人发的内容。关注会让对方的帖子更容易排到前面，但不会把其他人的帖子挡在外面。"
    "你可以发帖、评论、点赞、转发，也可以关注或取消关注别人。"
    "平台会保留用户发布的内容；当前参与者中没有官方账号，消息需要你自行判断。"
)
ROUND_TASK = (
    "先看你这一轮的推荐动态，再按自己的处境决定做什么："
    "想追问细节就在那条帖子下评论；看到了但没话说就点赞；"
    "有别人还没说过的新情况才发帖；想长期看某人动态就关注他。"
    "做什么、对谁做由你定，本轮至少要完成一次写入类动作。"
)
METRICS_NOTE = "帖子、评论、点赞、关注均为 SQL 中已提交的事实计数。"
