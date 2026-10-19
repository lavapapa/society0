"""在真实截图上叠加教程标注，保留原始截图与原始像素尺寸。"""
from pathlib import Path
import sys
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent / "2026-10-19"
FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"
# 标注坐标基于 2048×1200 的查看尺寸，绘制时映射回原始图片。
STEPS = [
    ("07-assistant-model", "可选｜选择 WorkBuddy 助手模型", "① 输入框下方的模型菜单　② 本轮选用 Flash；可按个人偏好选择，实验模型另外配置。",
     [(830, 1105, 1030, 1148), (650, 872, 1025, 912)]),
    ("02-config-change", "步骤 2｜检查配置，将变更交给 AI", "① 选主体　② 修改人设　③ 核对变更　④ 复制变更，返回会话粘贴发送。",
     [(16, 376, 210, 427), (782, 648, 1236, 695), (1700, 275, 2028, 400), (1927, 125, 2037, 157)]),
    ("03-real-network", "步骤 3｜沿关系网络查看对象", "① 切换版本与轮次　② 筛选关系类型　③ 全图、缩放与重排　④ 点击对象查看详情。",
     [(1480, 123, 1940, 158), (20, 209, 211, 333), (800, 1118, 1110, 1164), (1695, 174, 2034, 423)]),
    ("04-real-posts", "步骤 4｜浏览帖子与回复", "① 选用户　② 点帖子　③ 阅读正文与回复；顶部轮次决定当前查看的保存时点。",
     [(17, 207, 211, 507), (229, 204, 1456, 287), (1475, 208, 2032, 839)]),
    ("05-real-session", "步骤 5｜把信息机制与真实会话对照", "① 选主体与轮次　② 检查信息来源及其组件　③ 展开完整原始记录，核对实际输入与动作。",
     [(20, 624, 210, 675), (236, 278, 1670, 776), (1704, 330, 2010, 640)]),
    ("06-real-results", "步骤 6｜检查运行产物与用量", "① 选社交平台环境　② 打开运行产物　③ 查模型、状态与用量　④ 查保存文件；费用为估算。",
     [(18, 284, 209, 335), (378, 207, 450, 240), (238, 251, 1668, 873), (238, 884, 978, 1183)]),
    ("08-open-artifact", "打开工作台｜点击 AI 回复中的产物卡片", "① 点击 workbench.html 卡片　② 右侧出现工作台　③ 点击右上角全屏，扩大检查空间。",
     [(318, 811, 704, 888), (1170, 120, 2038, 194), (1958, 15, 1992, 49)]),
    ("09-provider-file", "准备模型｜将取得的服务资料附加给 AI", "① 输入框左下角 ＋　② 添加文件　③ 本地文件；聊天模型用于决策，嵌入模型用于语义检索（可选）。",
     [(315, 1107, 353, 1147), (319, 882, 531, 917), (544, 885, 757, 928)]),
]

for stem, title, legend, boxes in STEPS:
    if len(sys.argv) > 1 and stem not in sys.argv[1:]:
        continue
    original = Image.open(ROOT / f"{stem}.png").convert("RGB")
    w, h = original.size
    scale = w / 2048
    band = 220
    canvas = Image.new("RGB", (w, h + band), "#f3f6fa")
    canvas.paste(original, (0, band))
    draw = ImageDraw.Draw(canvas)
    draw.text((42, 28), title, font=ImageFont.truetype(FONT, 58), fill="#17324d")
    draw.text((42, 116), legend, font=ImageFont.truetype(FONT, 39), fill="#33465a")
    draw.line((0, band - 2, w, band - 2), fill="#c9d3df", width=3)
    for n, (x1, y1, x2, y2) in enumerate(boxes, 1):
        rect = (round(x1 * scale), round(y1 * h / 1200) + band,
                round(x2 * scale), round(y2 * h / 1200) + band)
        draw.rounded_rectangle(rect, radius=10, outline="#d92d35", width=6)
        # 编号贴在框角，说明放在顶部，避免遮挡主体内容。
        x, y = rect[0] + 7, rect[1] - 24
        draw.ellipse((x, y, x + 49, y + 49), fill="#d92d35", outline="white", width=2)
        draw.text((x + 24, y + 24), str(n), font=ImageFont.truetype(FONT, 30), fill="white", anchor="mm")
    canvas.save(ROOT / f"{stem}-annotated.png")
    print(f"{stem}-annotated.png")
