# -*- coding: utf-8 -*-
r"""验 DESIGN.md §2.5 的对比度 —— 直接从 bot.py 读令牌，不写死。

之前这份脚本里的色值是手抄的，改了主题它就过期了。
现在从源码读，永远跟实际一致。
"""
import io
import re
import sys

SRC = r"D:\ai\qq_bot\bot.py"


def _s(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def lum(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(x * 2 for x in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _s(r) + 0.7152 * _s(g) + 0.0722 * _s(b)


def cr(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def hsl(h):
    h = h.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    import colorsys
    H, L, S = colorsys.rgb_to_hls(r, g, b)
    return H * 360


src = io.open(SRC, encoding="utf-8").read()
a = src.find("<style>"); b = src.find("</style>")
css = src[a:b]

m_light = re.search(r":root\{(.*?)\}", css, re.S)
m_dark = re.search(r"@media \(prefers-color-scheme: dark\)\{\s*:root\{(.*?)\}\s*\}",
                   css, re.S)
if not m_light:
    print("  ❌ 读不到亮色令牌")
    sys.exit(1)
L = dict(re.findall(r"--([a-z0-9-]+):(#[0-9a-fA-F]{6})", m_light.group(1)))
D = dict(re.findall(r"--([a-z0-9-]+):(#[0-9a-fA-F]{6})", m_dark.group(1))) if m_dark else {}

W = "#ffffff"
DBG = D.get("bg", "#17141a")

print("=" * 80)
print("亮色（白底）—— 令牌直接取自 bot.py 的 :root")
print("=" * 80)
LIGHT_CHECKS = [
    ("azure", 4.5, "主色/链接"),
    ("azure-strong", 4.5, "主色 hover"),
    ("azure-deep", 4.5, "深蓝"),
    ("gold", 4.5, "金色（做文字）"),
    ("gold-bright", 3.0, "金色（仅图形）"),
    ("text", 7.0, "正文"),
    ("text-2", 4.5, "次要文字"),
    ("text-3", 3.0, "弱文字"),
    ("green", 4.5, "成功"),
    ("amber", 4.5, "警告"),
    ("red", 4.5, "错误"),
]
bad = []
for k, need, note in LIGHT_CHECKS:
    v = L.get(k)
    if not v:
        print("  ??   %-14s 令牌缺失" % k)
        continue
    r = cr(v, W)
    ok = r >= need
    if not ok:
        bad.append((k, v, r, need))
    print("  %s %-14s %s  %5.2f:1  门槛 %.1f   %s"
          % ("OK  " if ok else "FAIL", k, v, r, need, note))

# 特别声明：gold-pale 不能做文字
gp = L.get("gold-pale")
if gp:
    r = cr(gp, W)
    print()
    print("  %s gold-pale      %s  %5.2f:1  ← 她的发色，**声明禁止做文字**"
          % ("OK  " if r < 4.5 else "⚠  ", gp, r))

print()
print("=" * 80)
print("暗色（底 %s）" % DBG)
print("=" * 80)
DARK_CHECKS = [("azure", 4.5), ("azure-strong", 4.5), ("gold", 4.5),
               ("text", 7.0), ("text-2", 4.5), ("text-3", 3.0),
               ("green", 4.5), ("amber", 3.0), ("red", 4.5)]
for k, need in DARK_CHECKS:
    v = D.get(k)
    if not v:
        print("  ??   %-14s 令牌缺失" % k)
        continue
    r = cr(v, DBG)
    ok = r >= need
    if not ok:
        bad.append(("dark:" + k, v, r, need))
    print("  %s %-14s %s  %5.2f:1  门槛 %.1f"
          % ("OK  " if ok else "FAIL", k, v, r, need))

print()
print("=" * 80)
print("色相差：主色 vs 状态色（必须 ≥25°）")
print("=" * 80)
az = L.get("azure")
if az:
    ha = hsl(az)
    print("  主色 --azure %s  H=%.0f°" % (az, ha))
    for k in ("green", "amber", "red"):
        v = L.get(k)
        if not v:
            continue
        d = abs(hsl(v) - ha)
        ok = d >= 25
        if not ok:
            bad.append(("hue:" + k, v, d, 25))
        print("  %s %-8s %s H=%5.1f°  差 %5.1f°" % ("OK  " if ok else "FAIL", k, v, hsl(v), d))

print()
if bad:
    print("  ❌ %d 项不达标：" % len(bad))
    for k, v, r, need in bad:
        print("      %-16s %s  %.2f < %.1f" % (k, v, r, need))
    sys.exit(1)
print("  ✅ 全部达标（DESIGN.md §2.5 与 §2.4 的表格即以此为准）")
