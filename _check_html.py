# -*- coding: utf-8 -*-
r"""HTML 结构检查：把 admin 页里 <main> 那一段抽出来，做标签配平 + 嵌套检查。

之前删 page-points 时正则只吃到卡片的 </div>，留下一个孤立闭合标签，
提前关掉了 main，导致后面所有页面（含插件页）跑到容器外 —— 插件页整个空白。
这种错语法检查抓不到，必须专门查。
"""
import io
import os
import re
import sys

BASE = r"D:\ai\qq_bot"
P = os.path.join(BASE, "bot.py")
s = io.open(P, encoding="utf-8").read()

# 取 <main class="main"> ... </main>
i = s.find('<main class="main">')
j = s.find("</main>", i)
if i < 0 or j < 0:
    sys.exit("  ❌ 找不到 main")
body = s[i:j + len("</main>")]
print(f"  main 段 {len(body)} 字符")

# 也检查整个 body（aside + main）
bi = s.find("<body")
bj = s.find("</body>")
whole = s[bi:bj + 7] if bi >= 0 and bj > bi else body
print(f"  body 段 {len(whole)} 字符")

TAGS = ("div", "table", "thead", "tbody", "tr", "td", "th",
        "nav", "aside", "main", "section", "form", "select", "button")


def analyze(html, label):
    """返回 (深度轨迹, 最小深度, 结束深度, 问题列表)。"""
    events = []
    for m in re.finditer(r"<(/?)(\w+)([^>]*?)(/?)>", html):
        closing, tag, attrs, selfclose = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
        if tag not in TAGS:
            continue
        if selfclose:
            continue
        line = html[:m.start()].count("\n") + 1
        events.append((closing == "/", tag, line))

    depth = 0
    mind = 0
    problems = []
    stack = []
    for is_close, tag, line in events:
        if not is_close:
            stack.append((tag, line))
            depth += 1
        else:
            if not stack:
                problems.append(f"第 {line} 行多出一个 </{tag}>（没有对应的开标签）")
                depth -= 1
                mind = min(mind, depth)
                continue
            top, tline = stack[-1]
            if top == tag:
                stack.pop()
                depth -= 1
            else:
                # 找栈里有没有匹配的
                found = None
                for k in range(len(stack) - 1, -1, -1):
                    if stack[k][0] == tag:
                        found = k
                        break
                if found is None:
                    problems.append(f"第 {line} 行多出一个 </{tag}>（栈顶是 <{top}>，"
                                    f"开于第 {tline} 行）")
                    depth -= 1
                    mind = min(mind, depth)
                else:
                    unclosed = stack[found + 1:]
                    problems.append(
                        f"第 {line} 行 </{tag}> 之前有 {len(unclosed)} 个标签没闭合："
                        + ", ".join(f"<{t}>@L{l}" for t, l in unclosed[:5]))
                    del stack[found:]
                    depth = len(stack)
    return depth, stack, problems


print()
print("=" * 66)
print("main 段结构")
print("=" * 66)
depth, stack, problems = analyze(body, "main")
print(f"  结束时深度 {depth}（应为 0）")
if stack:
    print(f"  未闭合标签 {len(stack)} 个:")
    for t, l in stack[:10]:
        print(f"    <{t}> 开于第 {l} 行（相对 main 段）")
if problems:
    print(f"  结构问题 {len(problems)} 个:")
    for p in problems[:12]:
        print("    ❌ " + p)
else:
    print("  ✅ 没有多余闭合标签")

# 每个 .page 的深度是否一致
print()
print("=" * 66)
print("各 .page 在 main 里的相对位置")
print("=" * 66)
emain = body
depth = 0
pages = []
for m in re.finditer(r"<(/?)(\w+)([^>]*?)(/?)>", emain):
    closing, tag, attrs, selfclose = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
    if tag not in TAGS or selfclose:
        continue
    line = body[:m.start()].count("\n") + 1
    if not closing:
        if tag == "div" and 'class="page"' in attrs:
            pid = re.search(r'id="([^"]+)"', attrs)
            pages.append((line, depth, pid.group(1) if pid else "?"))
        depth += 1
    else:
        depth -= 1

EXPECT = pages[0][1] if pages else 2
print(f"  （所有页面都应在这个深度：{EXPECT}）")
for line, d, pid in pages:
    flag = "✅" if d == EXPECT else f"❌ 深度 {d}（应与其它页一致）"
    print(f"  L{line:<5d} 深度 {d}  {pid:<18} {flag}")

# 总 div 配平
print()
print("=" * 66)
print("标签计数")
print("=" * 66)
for t in ("div", "table", "tr", "td", "th", "button"):
    o = len(re.findall(rf"<{t}[\s>]", body))
    c = len(re.findall(rf"</{t}>", body))
    flag = "✅" if o == c else f"❌ 差 {o - c}"
    print(f"  <{t}> 开 {o}  闭 {c}   {flag}")
