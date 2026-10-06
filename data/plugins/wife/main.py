# -*- coding: utf-8 -*-
"""今日老婆插件。

原来内置在主程序里：do_wife / _gen_wife_intro / _wife_change_cost +
4 个 DB 方法 + wife_records 建表 + 12 个命令词 + /api/wife 系列 +
调试页两个面板 + 配置分组。

现在整块搬进插件，**连数据表都是插件自己建的** —— 主程序里一行都不剩。
表名沿用 `wife_records`，所以老用户已有的抽老婆记录不会丢。
"""
import asyncio
import hashlib
import random
from datetime import date

WIFE_KW = {"今日老婆", "抽老婆", "我要老婆", "我的老婆"}
WIFE_CHANGE_KW = {
    "换老婆", "换个老婆", "换一个老婆", "再换老婆", "再换一个",
    "换lp", "换LP", "换Lp",
}

TABLE = "wife_records"


def setup(ctx):

    # ---------------- 建表（插件自己的表自己管） ----------------
    def _ensure_table():
        try:
            # 老版本的表没有 wife_id 列，直接重建（跟原来主程序里的行为一致）
            row = ctx.db_query(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE,))
            if row and "wife_id TEXT" not in (row[0].get("sql") or ""):
                ctx.warn(f"检测到旧 {TABLE} 表，正在重建…")
                ctx.db_exec(f"DROP TABLE {TABLE}")
                row = []
            if row:
                cols = [r["name"] for r in ctx.db_query(f"PRAGMA table_info({TABLE})")]
                if "introduction" not in cols:
                    ctx.db_exec(f"ALTER TABLE {TABLE} ADD COLUMN introduction TEXT")
            ctx.db_exec(f"""CREATE TABLE IF NOT EXISTS {TABLE}(
                user_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
                wife_id TEXT, wife_nick TEXT,
                date TEXT NOT NULL, changes INTEGER DEFAULT 0,
                introduction TEXT,
                PRIMARY KEY (user_id, group_id, date))""")
            return True
        except Exception as e:
            ctx.warn(f"建表失败 {type(e).__name__}: {e}")
            return False

    _ensure_table()

    # ---------------- 配置读取 ----------------
    def _bool(key, d):
        v = ctx.cfg(key, d)
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "on", "开启")
        return bool(v)

    def _int(key, d):
        try:
            v = ctx.cfg(key, d)
            return d if v is None or v == "" else int(float(v))
        except Exception:
            return d

    def _float(key, d):
        try:
            v = ctx.cfg(key, d)
            return d if v is None or v == "" else float(v)
        except Exception:
            return d

    def _str(key, d):
        v = ctx.cfg(key, d)
        return d if v is None else str(v)

    # ---------------- 数据访问 ----------------
    def wife_get(uid, gid, today):
        r = ctx.db_query(
            f"SELECT * FROM {TABLE} WHERE user_id=? AND group_id=? AND date=?",
            (uid, gid, today))
        return r[0] if r else None

    def wife_save(uid, gid, wife_id, wife_nick, today, changes, introduction=""):
        ctx.db_exec(f"""INSERT INTO {TABLE}
                        (user_id, group_id, wife_id, wife_nick, date, changes, introduction)
                        VALUES(?,?,?,?,?,?,?)
                        ON CONFLICT(user_id,group_id,date) DO UPDATE SET
                          wife_id=excluded.wife_id,
                          wife_nick=excluded.wife_nick,
                          changes=excluded.changes,
                          introduction=excluded.introduction""",
                    (uid, gid, str(wife_id), wife_nick, today,
                     int(changes or 0), introduction))

    def wife_try_change(uid, gid, today, limit, amount=1):
        """占一个换老婆名额。返回新的已换次数；没名额或抢不到返回 None。"""
        row = ctx.db_query(
            f"SELECT changes FROM {TABLE} WHERE user_id=? AND group_id=? AND date=?",
            (uid, gid, today))
        cur_changes = (row[0].get("changes") or 0) if row else 0
        if row and cur_changes >= limit:
            return None
        new_changes = cur_changes + max(1, int(amount))
        if not row:
            try:
                ctx.db_exec(f"""INSERT INTO {TABLE}
                                (user_id, group_id, wife_id, wife_nick, date, changes, introduction)
                                VALUES(?,?,'','',?,?,'')""",
                            (uid, gid, today, new_changes))
                return new_changes
            except Exception:
                return None
        n = ctx.db_exec(
            f"UPDATE {TABLE} SET changes=? WHERE user_id=? AND group_id=? "
            f"AND date=? AND changes=?",
            (new_changes, uid, gid, today, cur_changes))
        return new_changes if n and n > 0 else None

    def wife_clear(gid):
        ctx.db_exec(f"DELETE FROM {TABLE} WHERE group_id=?", (gid,))

    def change_cost(changes):
        base = _int("wife_change_cost_base", 1)
        if changes <= 0:
            return 0
        return base * (2 ** min(changes - 1, 20))

    # ---------------- AI 写介绍 ----------------
    async def gen_intro(role_name):
        if not _bool("wife_intro_enabled", True):
            return ""
        prompt = (
            f"请用一句 15-30 字的中文，温柔浪漫地描述二次元角色「{role_name}」给人的感觉。\n"
            f"直接输出描述文字，不要引号，不要括号，不要换行，不要提到 AI 或模型。"
        )
        try:
            r = await asyncio.wait_for(ctx.ai(prompt, timeout=15.0), timeout=18.0)
        except asyncio.TimeoutError:
            ctx.warn("生成介绍超时")
            return ""
        except Exception as e:
            ctx.warn(f"生成介绍失败 {type(e).__name__}")
            return ""
        if not r:
            return ""
        r = r.strip().strip('"').strip("「」『』").replace("\n", " ")
        return r[:80]

    # ---------------- 主逻辑 ----------------
    async def do_wife(group_id, user_id, change=False):
        if not _bool("wife_enabled", True):
            return {"ok": False, "error": "今日老婆功能已关闭"}
        gid = int(group_id)
        uid = int(user_id)
        today = date.today().isoformat()
        limit = _int("wife_change_limit", 3)
        api_url = _str("wife_api_url", "https://api.pearapi.ai/api/today_wife")
        api_timeout = _float("wife_api_timeout", 15)

        row = wife_get(uid, gid, today)
        points_spent = 0

        def cached_reply(r):
            used = r.get("changes") or 0
            return {
                "ok": True, "user_id": uid, "group_id": gid,
                "role_name": r.get("wife_nick") or "未知",
                "image_url": str(r.get("wife_id") or ""),
                "introduction": r.get("introduction") or "",
                "changes_used": used,
                "changes_left": max(0, limit - used),
                "is_new": False, "cached": True,
                "message": f"你的今日老婆是 {r.get('wife_nick') or '未知'}",
            }

        def refund(reason):
            if points_spent > 0:
                ctx.points_refund(uid, gid, points_spent)
                ctx.warn(f"{uid} {reason}，已退还 {points_spent} 积分")

        try:
            if not change:
                if row and (row.get("changes") or 0) == 0:
                    img = str(row.get("wife_id") or "")
                    if img.startswith(("http://", "https://")):
                        return cached_reply(row)
                if row:
                    ctx.warn(f"{uid} 缓存无效，重新抽（保留已换 "
                             f"{row.get('changes') or 0} 次）")
                changes = (row.get("changes") or 0) if row else 0
                is_new = row is None
                info = ctx.sign_get(uid, gid)
                points_left = (info or {}).get("points") or 0
            else:
                used = (row.get("changes") or 0) if row else 0
                if used >= limit:
                    return {"ok": False, "error": f"今天已经换过 {limit} 次了"}
                changes = used + 1
                points_left = 0
                cost = change_cost(changes)
                if cost > 0:
                    ok, left = ctx.points_spend(uid, gid, cost)
                    if not ok:
                        return {
                            "ok": False,
                            "error": f"积分不足，本次换老婆需要 {cost} 积分，"
                                     f"你只有 {left} 积分。先签到攒分吧~",
                            "required": cost, "have": left,
                        }
                    points_left = left
                    points_spent = cost
                else:
                    info = ctx.sign_get(uid, gid)
                    points_left = (info or {}).get("points") or 0
                is_new = False
                reserved = wife_try_change(uid, gid, today, limit, 1)
                if reserved is None:
                    refund("名额被占用")
                    return {"ok": False, "error": "手速太快啦，稍后重试"}
                changes = reserved

            raw_seed = f"{gid}|{uid}|{today}|{changes}|{random.randint(1, 10 ** 9)}"
            wife_seed = hashlib.md5(raw_seed.encode()).hexdigest()[:16]
            try:
                r = await ctx.http_get(api_url, params={"id": wife_seed},
                                       timeout=api_timeout)
                r.raise_for_status()
                resp = r.json()
            except asyncio.CancelledError:
                refund("任务被取消")
                raise
            except Exception as e:
                ctx.warn(f"API 请求失败: {type(e).__name__}: {e}")
                refund("API 请求失败")
                return {"ok": False, "error": f"老婆 API 请求失败: {type(e).__name__}"}

            if (not isinstance(resp, dict) or resp.get("code") != 200
                    or not resp.get("data")):
                msg = resp.get("msg", "未知错误") if isinstance(resp, dict) else "返回格式异常"
                ctx.warn(f"API 返回异常: {msg}")
                refund("API 返回异常")
                return {"ok": False, "error": f"API 返回异常: {msg}"}

            data = resp["data"]
            image_url = data.get("image_url", "")
            role_name = data.get("role_name", "未知")
            width = data.get("width", 0)
            height = data.get("height", 0)
            if not image_url:
                refund("API 未返回图片")
                return {"ok": False, "error": "API 未返回图片地址"}

            introduction = await gen_intro(role_name)
            wife_save(uid, gid, image_url, role_name, today, changes, introduction)
            ctx.log(f"{uid} 抽到 {role_name}（第 {changes + 1} 次，"
                    f"消耗 {points_spent} 分）")

            return {
                "ok": True, "user_id": uid, "group_id": gid,
                "role_name": role_name, "image_url": image_url,
                "introduction": introduction, "width": width, "height": height,
                "changes_used": changes,
                "changes_left": max(0, limit - changes),
                "cost": points_spent, "points_left": points_left,
                "is_new": is_new, "is_change": change, "cached": False,
                "message": f"{'换' if change else '抽'}老婆成功！今日老婆是 {role_name}",
            }
        except asyncio.CancelledError:
            refund("任务被取消")
            raise
        except Exception as e:
            ctx.warn(f"未预期错误 {type(e).__name__}: {e}")
            refund("出错")
            return {"ok": False, "error": f"出错：{type(e).__name__}"}

    # ---------------- 群命令 ----------------
    async def run_cmd(event, change):
        ctx.log(f"{event.user_id} {'换' if change else '抽'}老婆")
        try:
            r = await asyncio.wait_for(
                do_wife(event.group_id, event.user_id, change=change), timeout=40.0)
        except asyncio.TimeoutError:
            ctx.warn(f"{event.user_id} 超时")
            await ctx.send_group(event.group_id,
                                 "换老婆响应超时，稍后再试~" if change
                                 else "老婆服务响应超时，稍后再试~")
            return True
        except Exception as e:
            ctx.warn(f"异常: {type(e).__name__}: {e}")
            await ctx.send_group(event.group_id,
                                 f"{'换老婆' if change else '抽老婆'}出错：{type(e).__name__}")
            return True

        if r.get("ok"):
            head = (f" 换老婆成功！今日老婆是 {r['role_name']}\n" if change
                    else f" 你的今日老婆是 {r['role_name']}\n")
            parts = [ctx.mention(event.user_id), head]
            if r.get("introduction"):
                parts.append(f"「{r['introduction']}」\n")
            if change and r.get("cost", 0) > 0:
                parts.append(f"消耗 {r['cost']} 积分，剩余 {r['points_left']} 分\n")
            parts.append(ctx.image(r["image_url"]))
            await ctx.send_group(event.group_id, parts)
        else:
            text = r.get("error", "失败")
            if change and r.get("required"):
                text += f"\n本次需要 {r['required']} 积分，你只有 {r['have']} 分。"
            await ctx.send_group(event.group_id, text)
        return True

    @ctx.command("今日老婆", "抽今日老婆（也认「抽老婆 / 我要老婆 / 我的老婆」）")
    async def on_wife(event, cmd):
        if cmd not in WIFE_KW:
            return False
        return await run_cmd(event, False)

    for _kw in sorted(WIFE_KW - {"今日老婆"}):
        def _mk(kw):
            async def _h(event, cmd, _kw=kw):
                return await on_wife(event, cmd)
            return _h
        ctx.command(_kw, "抽今日老婆")(_mk(_kw))

    @ctx.command("换老婆", "用积分换一个（也认「换个老婆 / 换lp」等）")
    async def on_change(event, cmd):
        if cmd not in WIFE_CHANGE_KW:
            return False
        return await run_cmd(event, True)

    for _kw in sorted(WIFE_CHANGE_KW - {"换老婆"}):
        def _mk2(kw):
            async def _h(event, cmd, _kw=kw):
                return await on_change(event, cmd)
            return _h
        ctx.command(_kw, "换今日老婆")(_mk2(_kw))

    # ---------------- 管理接口 ----------------
    async def _api(event=None, request=None, body=None):
        pass

    async def _api_wife(request, body):
        q = request.query_params
        b = body or {}
        gid = q.get("group_id") or b.get("group_id")
        uid = q.get("user_id") or b.get("user_id")
        if not gid or not uid:
            return {"ok": False, "error": "缺少 group_id 或 user_id"}
        ch = str(q.get("change") or b.get("change") or "").lower() in ("1", "true", "yes")
        return await do_wife(gid, uid, ch)

    async def _api_clear(request, body):
        q = request.query_params
        b = body or {}
        gid = q.get("group_id") or (b or {}).get("group_id")
        if not gid:
            return {"ok": False, "error": "缺少 group_id"}
        wife_clear(gid)
        ctx.log(f"清空群 {gid} 的老婆记录")
        return {"ok": True}

    ctx.register_api("/admin/api/plugins/wife/get", _api_wife, methods=("GET", "POST"))
    ctx.register_api("/admin/api/plugins/wife/clear", _api_clear, methods=("GET", "POST"))

    # ---------------- 配置项 ----------------
    ctx.register_config("今日老婆", [
        ["wife_enabled", "功能开关", "bool"],
        ["wife_change_limit", "每天换老婆次数上限", "number"],
        ["wife_change_cost_base", "基础消耗（第1次）", "number"],
        ["wife_intro_enabled", "自动生成介绍", "bool"],
        ["wife_api_url", "老婆 API 地址", "text"],
        ["wife_api_timeout", "API 超时（秒）", "number"],
    ])

    # ---------------- 插件自带页面 ----------------
    ctx.register_page("wife", "今日老婆", """
<div class="card">
  <div class="card-h"><h3>今日老婆</h3><span class="en">Today's Wife</span></div>
  <div class="page-desc">
    每天抽一个二次元老婆，带 AI 写的介绍和配图。<br>
    群成员 @机器人 发「<b>今日老婆</b>」抽，「<b>换老婆</b>」用积分换。<br>
    配置项在「系统配置 → 今日老婆」里。
  </div>
  <div class="form-grid">
    <div class="form-row"><label>群号</label>
      <input id="wf-gid" class="inp" placeholder="例如 100000000"></div>
    <div class="form-row"><label>QQ号</label>
      <input id="wf-uid" class="inp" placeholder="例如 10001"></div>
  </div>
  <div style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap">
    <button class="btn primary" onclick="wfRun(false)">抽/查今日老婆</button>
    <button class="btn" onclick="wfRun(true)">换一个</button>
    <button class="btn danger" onclick="wfClear()">清空本群记录</button>
  </div>
  <div class="api-result" id="wf-res">等待请求…</div>
</div>
<script>
function wfBox(v){var b=document.getElementById("wf-res");if(b)b.textContent=v;}
async function wfRun(change){
  var g=document.getElementById("wf-gid"),u=document.getElementById("wf-uid");
  if(!g.value||!u.value){wfBox("请填群号和 QQ 号");return;}
  wfBox(change?"换老婆中…":"抽老婆中…（要调外部 API，可能要几秒）");
  var r=await api("/admin/api/plugins/wife/get?group_id="+encodeURIComponent(g.value)
                  +"&user_id="+encodeURIComponent(u.value)
                  +(change?"&change=1":""));
  if(r&&r.ok&&r.image_url){
    wfBox("【"+(r.role_name||"?")+"】\\n"+(r.introduction||"")+"\\n\\n"+r.image_url);
  }else{
    wfBox(JSON.stringify(r,null,2));
  }
}
async function wfClear(){
  var g=document.getElementById("wf-gid");
  if(!g.value){wfBox("请填群号");return;}
  if(!confirm("清空该群所有老婆记录？不可恢复。"))return;
  var r=await api("/admin/api/plugins/wife/clear?group_id="+encodeURIComponent(g.value),
                  {method:"POST"});
  wfBox(JSON.stringify(r,null,2));
}
</script>
""")

    ctx.log("今日老婆插件已就绪（命令：今日老婆 / 抽老婆 / 换老婆 …）")
