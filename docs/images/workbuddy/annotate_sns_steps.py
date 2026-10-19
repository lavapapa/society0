"""保留原始 SNS 截图，在顶部添加说明，并用红框标出操作位置。"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent / "sns-2026-10-19"
FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"
# 裁剪用于突出当前操作；原始完整截图始终保留。
STEPS = [
    ("00-provider", "准备模型资料｜添加自己的本地文件", "点击输入框旁的＋后，选择 ① 添加文件 → ② 本地文件；内容按正文示例填写。",
     (0.14, 0.71, 0.52, 0.97), [(0.045, 0.045, 0.32, 0.19), (0.34, 0.07, 0.62, 0.22)]),
    ("01-open-artifact", "打开工作台｜点击产物卡片，在右侧预览", "① workbench.html 卡片　② 右上角全屏；页面空白时，可从 ⋯ 菜单刷新。",
     None, [(0.155, 0.414, 0.50, 0.477), (0.958, 0.013, 0.973, 0.04)]),
    ("02-config-change", "修改设定｜核对原值与拟改值，再复制", "① 编辑人设　② 查看变更　③ 复制后发给 AI，新建配置版本。",
     None, [(0.475, 0.51, 0.755, 0.582), (0.79, 0.155, 0.99, 0.26), (0.925, 0.005, 0.995, 0.038)]),
    ("03-plugins", "检查插件｜按用途查看，按需要展开", "① 插件配置　② 内置保持启用　③ info 查看实现信息，“参数”进入配置页。",
     None, [(0.012, 0.455, 0.127, 0.487), (0.146, 0.103, 0.352, 0.25), (0.66, 0.45, 0.764, 0.488)]),
    ("04-memory", "调整记忆｜在独立页面编辑参数", "① 长期记忆　② 召回上限　③ 右侧核对 3 → 4，再复制变更。",
     None, [(0.012, 0.23, 0.127, 0.26), (0.475, 0.66, 0.755, 0.71), (0.79, 0.155, 0.99, 0.23)]),
    ("05-network", "阅读关系｜选择图层与对象，追查互动", "① 关系图层　② 浮动工具条　③ 帖子正文、回复与关联记录。",
     None, [(0.012, 0.09, 0.126, 0.205), (0.36, 0.92, 0.554, 0.969), (0.79, 0.09, 0.99, 0.31)]),
    ("06-posts", "浏览帖子｜从内容进入作者与会话", "① 全部用户或指定用户　② 帖子列表　③ 正文、回复和关联记录。",
     None, [(0.008, 0.08, 0.13, 0.49), (0.14, 0.09, 0.64, 0.25), (0.65, 0.09, 0.99, 0.43)]),
    ("07-session", "追查行动｜对应用户、轮次与真实会话", "① 用户　② 当前轮次　③ 可接触的信息机制　④ 实际输入与工具记录。",
     None, [(0.012, 0.52, 0.127, 0.566), (0.87, 0.004, 0.92, 0.039), (0.142, 0.16, 0.772, 0.455), (0.788, 0.09, 0.991, 0.48)]),
    ("08-results", "检查产物｜确认状态、用量与费用估算", "① 社交平台环境　② 运行产物　③ 完成状态、调用用量及原始资料。",
     None, [(0.012, 0.167, 0.127, 0.20), (0.23, 0.085, 0.273, 0.118), (0.477, 0.13, 0.773, 0.97)]),
    ("09-analysis", "阅读分析｜先核对事实，再讨论解释", "① 运行状态　② 帖子与动作记录；引用 Thread 和事件序号，可以返回工作台查原文。",
     (0.525, 0.10, 0.999, 0.98), [(0.005, 0.295, 0.99, 0.46), (0.005, 0.48, 0.99, 0.82)]),
]

for stem, title, legend, crop, boxes in STEPS:
    original = Image.open(ROOT / f"{stem}.png").convert("RGB")
    if crop:
        w, h = original.size
        original = original.crop(tuple(round(v * (w if i % 2 == 0 else h)) for i, v in enumerate(crop)))
    if original.width > 1800:
        original.thumbnail((1800, 1800 * original.height // original.width))
    width, height = original.size
    scale = width / 1440
    band = round(110 * scale)
    canvas = Image.new("RGB", (width, height + band), "#f3f6fa")
    canvas.paste(original, (0, band))
    draw = ImageDraw.Draw(canvas)
    draw.text((24 * scale, 15 * scale), title, font=ImageFont.truetype(FONT, round(27 * scale)), fill="#17324d")
    draw.text((24 * scale, 62 * scale), legend, font=ImageFont.truetype(FONT, round(17 * scale)), fill="#33465a")
    for number, (x1, y1, x2, y2) in enumerate(boxes, 1):
        rect = (round(x1 * width), round(y1 * height) + band, round(x2 * width), round(y2 * height) + band)
        draw.rounded_rectangle(rect, radius=round(4 * scale), outline="#d92d35", width=max(2, round(2 * scale)))
        x, y = rect[0] + 12 * scale, rect[1] + 12 * scale
        radius = 11 * scale
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill="#d92d35")
        draw.text((x, y), str(number), font=ImageFont.truetype(FONT, round(14 * scale)), fill="white", anchor="mm")
    canvas.save(ROOT / f"{stem}-annotated.png", optimize=True)
