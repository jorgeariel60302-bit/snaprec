"""生成企鹅系列图标：SnapTool（截图，蓝）和 SnapRec（录屏，红）。

用法：pip install cairosvg pillow，然后 python tools/make_icons.py 输出目录
会生成 snaptool.* 和 snaprec.*（svg / png / ico），分别复制为各仓库的 assets/icon.*。
"""
import io, sys
import cairosvg
from PIL import Image

PENGUIN = """
  <!-- 鳍 -->
  <ellipse cx="66" cy="168" rx="15" ry="36" fill="#1b2540" transform="rotate(22 66 168)"/>
  <ellipse cx="190" cy="168" rx="15" ry="36" fill="#1b2540" transform="rotate(-22 190 168)"/>
  <!-- 脚 -->
  <ellipse cx="104" cy="226" rx="20" ry="10" fill="#ff9f1c"/>
  <ellipse cx="152" cy="226" rx="20" ry="10" fill="#ff9f1c"/>
  <!-- 身体 -->
  <ellipse cx="128" cy="150" rx="70" ry="80" fill="#1f2a48"/>
  <!-- 肚子和脸 -->
  <ellipse cx="128" cy="168" rx="50" ry="56" fill="#ffffff"/>
  <circle cx="106" cy="118" r="28" fill="#ffffff"/>
  <circle cx="150" cy="118" r="28" fill="#ffffff"/>
  <!-- 眼睛 -->
  <circle cx="108" cy="116" r="10" fill="#1b2540"/>
  <circle cx="148" cy="116" r="10" fill="#1b2540"/>
  <circle cx="111" cy="112" r="3.5" fill="#ffffff"/>
  <circle cx="151" cy="112" r="3.5" fill="#ffffff"/>
  <!-- 脸颊 -->
  <circle cx="90" cy="136" r="8" fill="#ffadbd" opacity="0.85"/>
  <circle cx="166" cy="136" r="8" fill="#ffadbd" opacity="0.85"/>
  <!-- 嘴 -->
  <path d="M113 130 Q128 123 143 130 Q135 147 128 149 Q121 147 113 130 Z" fill="#ff9f1c"/>
"""

def svg(bg_top, bg_bottom, badge):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{bg_top}"/><stop offset="1" stop-color="{bg_bottom}"/>
    </linearGradient>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="3" stdDeviation="3" flood-color="#000" flood-opacity="0.25"/>
    </filter>
  </defs>
  <rect x="8" y="8" width="240" height="240" rx="56" fill="url(#bg)"/>
  <g filter="url(#shadow)">{PENGUIN}</g>
  <g filter="url(#shadow)">{badge}</g>
</svg>"""

# 截图：四个取景角标
TOOL_BADGE = """
  <circle cx="200" cy="58" r="38" fill="#ffffff"/>
  <g fill="none" stroke="#1f6fd1" stroke-width="7" stroke-linecap="round" stroke-linejoin="round">
    <path d="M180 50 V40 H190"/><path d="M210 40 H220 V50"/>
    <path d="M220 66 V76 H210"/><path d="M190 76 H180 V66"/>
  </g>
  <circle cx="200" cy="58" r="6" fill="#1f6fd1"/>
"""
# 录屏：录制红点
REC_BADGE = """
  <circle cx="200" cy="58" r="38" fill="#ffffff"/>
  <circle cx="200" cy="58" r="22" fill="#e5322d"/>
"""

ICONS = {
    "snaptool": svg("#4aa3f0", "#1f63c9", TOOL_BADGE),
    "snaprec": svg("#ff7a59", "#d9302c", REC_BADGE),
}

def render(svg_text, size):
    png = cairosvg.svg2png(bytestring=svg_text.encode(), output_width=size, output_height=size)
    return Image.open(io.BytesIO(png)).convert("RGBA")

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    sizes = [16, 24, 32, 48, 64, 128, 256]
    for name, text in ICONS.items():
        open(f"{out}/{name}.svg", "w", encoding="utf-8").write(text)
        big = render(text, 256)
        big.save(f"{out}/{name}.png")
        # 每个尺寸单独渲染，小尺寸也清晰
        imgs = [render(text, s) for s in sizes]
        imgs[-1].save(f"{out}/{name}.ico", sizes=[(s, s) for s in sizes], append_images=imgs[:-1])
    print("ok")
