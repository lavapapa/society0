"""给真实插件配置截图添加步骤说明和红框，坐标按原图比例记录。"""
from pathlib import Path
import sys
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent / "2026-10-19"
FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"
STEPS = [
    ("13-compact-plugins", "查看插件｜先看用途，需要时再展开细节",
     "① 实验配置　② info 查看依赖与来源　③ 参数直达对应配置页。",
     [(0.012, 0.17, 0.14, 0.60), (0.40, 0.25, 0.423, 0.30), (0.63, 0.72, 0.67, 0.76)]),
    ("14-memory-settings", "调整参数｜在长期记忆页修改召回上限",
     "① 选择配置页　② 修改参数　③ 核对变更后复制，发给 AI 创建新版本。",
     [(0.012, 0.29, 0.14, 0.33), (0.16, 0.54, 0.74, 0.71), (0.76, 0.002, 0.99, 0.30)]),
    ("10-real-plugins", "检查插件｜从实验设置进入插件配置",
     "① 实验设置　② 插件配置　③ 两类插件与开关；内置保持启用，扩展按依赖联动。",
     [(0.52, 0.002, 0.60, 0.05), (0.012, 0.43, 0.14, 0.49), (0.155, 0.06, 0.30, 0.98)]),
    ("12-plugin-new-version", "核对新版本｜修改已由 AI 写入实验配置",
     "① 确认新版本　② 查看更新后的参数　③ 关闭的扩展仍保留入口，方便再次启用。",
     [(0.12, 0.002, 0.22, 0.05), (0.32, 0.80, 0.97, 0.95), (0.16, 0.88, 0.29, 0.95)]),
]

for stem, title, legend, boxes in STEPS:
    if len(sys.argv) > 1 and stem not in sys.argv[1:]:
        continue
    original = Image.open(ROOT / f"{stem}.png").convert("RGB")
    width, height = original.size
    scale = width / 1280
    band = round(100 * scale)
    canvas = Image.new("RGB", (width, height + band), "#f3f6fa")
    canvas.paste(original, (0, band))
    draw = ImageDraw.Draw(canvas)
    draw.text((24 * scale, 16 * scale), title, font=ImageFont.truetype(FONT, round(25 * scale)), fill="#17324d")
    draw.text((24 * scale, 57 * scale), legend, font=ImageFont.truetype(FONT, round(16 * scale)), fill="#33465a")
    for number, (x1, y1, x2, y2) in enumerate(boxes, 1):
        rect = (round(x1 * width), round(y1 * height) + band,
                round(x2 * width), round(y2 * height) + band)
        draw.rounded_rectangle(rect, radius=round(4 * scale), outline="#d92d35", width=max(2, round(2 * scale)))
        x, y = rect[0] + 10 * scale, rect[1] + 10 * scale
        radius = 11 * scale
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill="#d92d35")
        draw.text((x, y), str(number), font=ImageFont.truetype(FONT, round(14 * scale)), fill="white", anchor="mm")
    canvas.save(ROOT / f"{stem}-annotated.png")
