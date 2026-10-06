# -*- coding: utf-8 -*-
"""第 2 步：把缓存的 wikitext 解析成问答对。

输出 _wiki_entries.json：{"group_id":0,"items":[{question,answer,tags}]}
问题里带"鸣潮"前缀，与已有条目风格一致；tags 交给 bot 自动分类。
"""
import io
import json
import os
import re

CACHE = r"D:\ai\qq_bot\_wiki_cache"
OUT = r"D:\ai\qq_bot\_wiki_entries.json"

# ---------- wikitext 工具 ----------
CLEAN_PATTERNS = [
    (re.compile(r'<!--.*?-->', re.S), ''),                 # 注释
    (re.compile(r'<ref[^>]*>.*?</ref>', re.S), ''),
    (re.compile(r'<ref[^>]*/>'), ''),
    (re.compile(r'\{\{#(?:if|vardefine|var|widget|ask|ifeq|switch|expr)[^}]*\}\}', re.S), ''),
    (re.compile(r'\{\{(?:颜色|Color|color)\|([^|}]*)[^}]*\}\}'), r'\1'),
    (re.compile(r'\[\[(?:[^\]|]*\|)?([^\]|]+)\]\]'), r'\1'),  # [[a|b]] -> b
    (re.compile(r"'''?"), ''),
    (re.compile(r'<[^>]+>'), ' '),
    (re.compile(r'&nbsp;?'), ' '),
    (re.compile(r'&#?\w{2,6};'), ' '),
    (re.compile(r'__[A-Z]+__'), ''),
]


def clean(v: str, pagename: str = "") -> str:
    s = str(v or '')
    # 先解析模板变量（必须在其它清洗之前，否则会被当残留删掉）
    if pagename:
        s = s.replace('{{SUBPAGENAME}}', pagename)
        s = s.replace('{{FULLPAGENAME}}', pagename)
        # 支持 {{SUBPAGENAME|默认值}}
        s = re.sub(r'\{\{SUBPAGENAME\|([^}]*)\}\}', lambda m: pagename, s)
    s = re.sub(r'\{\{PAGENAME\}\}', pagename, s)
    for pat, rep in CLEAN_PATTERNS:
        s = pat.sub(rep, s)
    s = re.sub(r'\{\{[^}]{0,40}\}\}', '', s)      # 清掉剩余的简单模板
    s = s.replace('{{', '').replace('}}', '')
    s = re.sub(r'[ \t\u3000]+', ' ', s)
    s = re.sub(r'\n{2,}', '\n', s)
    return s.strip(' \t\n|')


def template_block(wt: str, name: str) -> str:
    """取出 {{name ... }} 这一段（按大括号配对，避免嵌套模板截断）。"""
    key = "{{" + name
    i = wt.find(key)
    if i < 0:
        return ""
    j = i + len(key)
    depth = 1
    while j < len(wt) - 1:
        if wt[j] == '{' and wt[j + 1] == '{':
            depth += 1
            j += 2
            continue
        if wt[j] == '}' and wt[j + 1] == '}':
            depth -= 1
            if depth == 0:
                return wt[i:j + 2]
            j += 2
            continue
        j += 1
    return wt[i:]


def fields_of(block: str, pagename: str = "") -> dict:
    """只从给定模板块里提字段，避免跨模板串味。"""
    out = {}
    if not block:
        return out
    marks = [(m.start(), m.end(), m.group(1).strip())
             for m in re.finditer(r'\|([^=\n|]{1,24})=', block)]
    for i, (st, en, key) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(block)
        val = block[en:end].rstrip()
        val = re.sub(r'\}\}\s*$', '', val)
        val = clean(val, pagename)
        if key not in out or (len(val) > len(out[key])):
            out[key] = val
    return out


def fields(wt: str, pagename: str = "") -> dict:
    """整页提字段（仅用于单模板页面，如名词注释）。"""
    return fields_of(wt, pagename)


def name_of(title: str) -> str:
    return title.split('/')[-1].strip()


# ---------- 各类页面 -> 问答对 ----------
def from_term(rec):
    """名词注释：整页就是一条。"""
    n = name_of(rec['title'])
    f = fields_of(template_block(rec['wikitext'], '名词注释'), n)
    desc = f.get('描述') or f.get('注释') or ''
    if len(desc) < 6:
        return []
    tags = ['鸣潮', '名词注释']
    for k in ('注释分类', '二级分类'):
        if f.get(k):
            tags.append(f[k])
    return [{"question": f"鸣潮 {n} 是什么", "answer": desc,
             "tags": ",".join(tags)}]


def from_echo(rec):
    """声骸：拆成多条。"""
    n = name_of(rec['title'])
    f = fields_of(template_block(rec['wikitext'], '声骸'), n)
    n = f.get('名称') or n
    items = []

    def add(q, a):
        a = (a or '').strip()
        if len(a) >= 2:
            items.append({"question": q, "answer": a, "tags": "鸣潮,声骸," + n})

    basic = []
    for label, key in (("编号", "编号"), ("危险等级", "危险度"),
                       ("COST花费", "COST花费"), ("所属套装", "所属套装"),
                       ("获取途径", "获取途径"), ("实装版本", "实装版本")):
        if f.get(key):
            basic.append(f"{label}：{f[key]}")
    if basic:
        add(f"鸣潮声骸 {n} 的基础信息", "\n".join(basic))
    skill = f.get('技能简述') or f.get('5星声骸技能') or ''
    if skill:
        add(f"鸣潮声骸 {n} 的技能效果是什么", skill)
    if f.get('技能冷却'):
        add(f"鸣潮声骸 {n} 的技能冷却多久", f"{f['技能冷却']} 秒")
    return items


def from_resonator(rec):
    """共鸣者（角色）：按模板块分别解析，拆成多条。"""
    n = name_of(rec['title'])
    wt = rec['wikitext']
    basic = fields_of(template_block(wt, '共鸣者/基本资料'), n)
    stat = fields_of(template_block(wt, '共鸣者/基础属性'), n)
    skill = fields_of(template_block(wt, '共鸣者/技能'), n)
    team = fields_of(template_block(wt, '共鸣者/共鸣链'), n)
    n = basic.get('名称') or n
    items = []

    def add(q, a):
        a = (a or '').strip()
        if len(a) >= 2:
            items.append({"question": q, "answer": a, "tags": "鸣潮,角色图鉴," + n})

    rows = []
    for label, key in (("属性", "属性"), ("武器", "武器"), ("星级", "品质"),
                       ("生日", "生日"), ("出生地", "出生"), ("所属势力", "势力"),
                       ("共鸣能力", "共鸣能力"), ("实装日期", "实装日期"),
                       ("特殊料理", "特殊料理")):
        v = basic.get(key)
        if v:
            if key == "品质" and re.fullmatch(r'\d+', v):
                v += " 星"
            rows.append(f"{label}：{v}")
    # 战斗风格是内部编号（如 2,7,33,27,13），对问答没意义，单独处理
    fs = basic.get('战斗风格') or ''
    fs = re.sub(r'\d+', '', fs)          # 去掉纯编号
    fs = re.sub(r'[,\s]+', ' ', fs).strip()
    if fs and not re.fullmatch(r'[\d,\s]*', basic.get('战斗风格') or ''):
        rows.append(f"战斗风格：{fs}")
    elif (basic.get('战斗风格') or '').strip():
        # 全是编号时，用模板描述不了，跳过（不写进答案）
        pass
    if rows:
        add(f"鸣潮 {n} 的基本资料", "\n".join(rows))
    if basic.get('简介'):
        add(f"鸣潮 {n} 的简介", basic['简介'])
    st = [f"{k}：{stat[k]}" for k in ("生命", "攻击", "防御") if stat.get(k)]
    if st:
        add(f"鸣潮 {n} 的基础属性是多少", "\n".join(st))
    if basic.get('初奏'):
        add(f"鸣潮 {n} 的登场台词是什么", basic['初奏'])
    # 技能描述里残留的"技能"占位符（模板未展开的样子），去掉后可能变空
    def tidy_skill(s):
        # 只删"独立成段"的裸"技能"（模板未展开的占位符）。
        # 不能全局替换 —— "共鸣技能""变奏技能"里的"技能"有意义，
        # 删了会得到"施放时，每次攻击消耗…"这种残句（第一版就踩了这个坑）。
        s = str(s or '')
        parts = re.split(r'(?<=[，。、；：\n])|(?=[，。、；：\n])', s)
        kept = [seg for seg in parts if seg.strip() not in ('技能', '技能技能')]
        s = ''.join(kept)
        s = s.replace('技能技能', '技能')
        s = re.sub(r'[ \t]{2,}', ' ', s)
        s = re.sub(r'\n{2,}', '\n', s)
        s = re.sub(r'^[，。、；：\s/]+', '', s)
        return s.strip()

    for key, label in (("常态攻击", "常态攻击"), ("共鸣技能", "共鸣技能"),
                       ("共鸣回路", "共鸣回路"), ("共鸣解放", "共鸣解放"),
                       ("变奏技能", "变奏技能"), ("延奏技能", "延奏技能")):
        d = tidy_skill(skill.get(key + "描述"))
        if d and len(d) >= 8:
            add(f"鸣潮 {n} 的{label}是什么", d)
    # 共鸣链（命座）
    for i in range(1, 7):
        dv = team.get(f"共鸣链{i}") or team.get(f"第{i}层") or team.get(str(i))
        if dv and len(dv) >= 6:
            add(f"鸣潮 {n} 的共鸣链第{i}层是什么", dv)
    return items


def from_narrative(rec):
    """角色经历 / 设定拾遗 / 社区回响 / 简介：叙述性内容。"""
    n = name_of(rec['title'])
    txt = clean(rec['wikitext'], n)
    # 去掉小标题符号和多余空白
    txt = re.sub(r'^\s*=+\s*(.+?)\s*=+\s*$', r'【\1】', txt, flags=re.M)
    txt = re.sub(r'^\s*\*\s*', '· ', txt, flags=re.M)
    txt = re.sub(r'\n{2,}', '\n', txt).strip()
    if len(txt) < 20:
        return []
    cat = rec['cat']
    prefix = {"角色经历": "的角色经历", "设定拾遗": "的设定",
              "社区回响": "的社区评价", "简介": "的简介",
              "世界观": "的设定"}.get(cat, "的设定")
    return [{"question": f"鸣潮 {n}{prefix}",
             "answer": txt[:4000],
             "tags": f"鸣潮,{cat},{n}"}]


BUILDERS = {
    "名词注释": from_term,
    "声骸": from_echo,
    "共鸣者": from_resonator,
    "角色经历": from_narrative,
    "设定拾遗": from_narrative,
    "社区回响": from_narrative,
    "简介": from_narrative,
    "世界观": from_narrative,
    "剧情角色": from_narrative,
}


def main():
    recs = []
    for fn in os.listdir(CACHE):
        if not fn.endswith(".json"):
            continue
        try:
            r = json.load(io.open(os.path.join(CACHE, fn), encoding="utf-8"))
        except Exception:
            continue
        if r.get("ok"):
            recs.append(r)

    all_items, stat, empty = [], {}, []
    seen_q = {}
    for r in recs:
        b = BUILDERS.get(r["cat"])
        if not b:
            continue
        try:
            items = b(r)
        except Exception as e:
            empty.append((r["title"], f"{type(e).__name__}: {e}"))
            continue
        if not items:
            empty.append((r["title"], "解析出 0 条"))
            continue
        stat[r["cat"]] = stat.get(r["cat"], 0) + len(items)
        for it in items:
            q = it["question"].strip()
            # 去掉答案里的维基残留，并限长
            a = re.sub(r'\s*\n\s*', '\n', it["answer"]).strip()
            a = a[:4000]
            if len(a) < 4 or not q:
                continue
            it["question"], it["answer"] = q, a
            if q in seen_q:
                continue
            seen_q[q] = 1
            all_items.append(it)

    json.dump({"group_id": 0, "items": all_items},
              io.open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"页面 {len(recs)} 个 -> 问答对 {len(all_items)} 条")
    print()
    print("按类型:")
    for k, v in sorted(stat.items(), key=lambda x: -x[1]):
        print(f"  {k:<8} {v:>4} 条")
    print()
    if empty:
        print(f"解析为空的页面 {len(empty)} 个（前 12）:")
        for t, why in empty[:12]:
            print(f"  {t}  ->  {why}")
    # 长度分布
    lens = sorted(len(i["answer"]) for i in all_items)
    if lens:
        print()
        print(f"答案长度: 最短 {lens[0]} / 中位 {lens[len(lens)//2]} / 最长 {lens[-1]}")
        print(f"输出: {OUT}  ({os.path.getsize(OUT)/1024:.0f} KB)")


main()
