# -*- coding: utf-8 -*-
"""签到积分插件。

原来内置在主程序里：do_sign / do_sign_rank / do_sign_me + 9 个命令词 +
/api/sign 系列 + 「签到积分」管理页 + 调试页三个面板 + 超管「清空积分」。

现在整块搬进插件。**积分数据层留在主程序**（points 表、原子签到、扣分退款）——
扣分要被「今日老婆」用，属于公共能力。
"""
from datetime import date

SIGN_KW = {"签到", "打卡"}
RANK_KW = {"签到排行", "积分排行", "排行榜", "签到榜", "积分榜"}
ME_KW = {"我的积分", "我的签到"}
CLEAR_KW = {"清空积分", "清积分"}


def setup(ctx):

    def _bool(key, default):
        v = ctx.cfg(key, default)
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "on", "开启")
        return bool(v)

    def _int(key, default):
        try:
            v = ctx.cfg(key, default)
            if v is None or v == "":
                return default
            return int(float(v))
        except Exception:
            return default

    async def do_sign(gid, uid, nickname=""):
        """签一次到。"""
        if not _bool("sign_enabled", True):
            return {"ok": False, "error": "签到功能已关闭"}
        gid = int(gid)
        uid = int(uid)
        today = date.today().isoformat()

        if not nickname:
            nickname = str(uid)
        base = _int("sign_base_points", 10)
        bonus_per = _int("sign_streak_bonus", 2)
        bonus_cap = _int("sign_max_bonus", 20)

        try:
            r = ctx.sign_do(uid, gid, nickname, base, bonus_per, bonus_cap, today)
        except Exception as e:
            ctx.warn(f"签到写入失败 {type(e).__name__}: {e}")
            return {"ok": False, "error": "签到写入失败，请稍后再试"}

        if r.get("already"):
            return {
                "ok": False, "error": "今天已经签到过了",
                "points": r.get("total"), "streak": r.get("streak"),
                "sign_count": r.get("sign_count", 0), "already_signed": True,
            }
        added = r.get("points_added", 0)
        total = r.get("total", 0)
        streak = r.get("streak", 0)
        ctx.log(f"{nickname}({uid}) +{added} 分，累计 {total}，连续 {streak} 天")
        return {
            "ok": True, "nickname": nickname, "user_id": uid, "group_id": gid,
            "points_added": added, "bonus": r.get("bonus", 0), "total": total,
            "streak": streak,
            "message": f"{nickname} 签到成功！本次 +{added} 分，"
                       f"累计 {total} 分，连续 {streak} 天。",
        }

    def do_rank(gid, limit=10):
        rows = ctx.sign_rank(gid, limit=max(1, min(int(limit or 10), 100)))
        return {
            "ok": True, "group_id": int(gid),
            "rank": [{"rank": i + 1, "user_id": r.get("user_id"),
                      "nickname": r.get("nickname"), "points": r.get("points"),
                      "sign_count": r.get("sign_count"), "streak": r.get("streak")}
                     for i, r in enumerate(rows)],
        }

    def do_me(gid, uid):
        info = ctx.sign_get(int(uid), int(gid))
        if not info:
            return {"ok": False, "error": "还没有签到记录"}
        return {
            "ok": True, "user_id": int(uid), "group_id": int(gid),
            "nickname": info.get("nickname"), "points": info.get("points"),
            "sign_count": info.get("sign_count"), "streak": info.get("streak"),
            "best_streak": info.get("best_streak") or 0,
            "last_sign_date": info.get("last_sign_date"),
        }

    # ---------------- 群命令 ----------------
    @ctx.command("签到", "每日签到（也认「打卡」）")
    async def on_sign(event, cmd):
        if cmd not in SIGN_KW:
            return False
        nick = ""
        try:
            nick = event.sender.card or event.sender.nickname or ""
        except Exception:
            pass
        r = await do_sign(event.group_id, event.user_id, nick)
        await ctx.send_group(event.group_id,
                             r.get("message") if r.get("ok") else r.get("error", "签到失败"))
        return True

    @ctx.command("打卡", "每日签到")
    async def on_daka(event, cmd):
        return await on_sign(event, cmd)

    @ctx.command("签到排行", "本群积分排行 TOP10")
    async def on_rank(event, cmd):
        if cmd not in RANK_KW:
            return False
        r = do_rank(event.group_id, 10)
        rows = r.get("rank", [])
        if not rows:
            text = "本群还没有人签到过。"
        else:
            lines = [f"本群签到排行 TOP{len(rows)}："]
            for i, x in enumerate(rows):
                prefix = ["1.", "2.", "3."][i] if i < 3 else f"{i+1}."
                lines.append(f"  {prefix} {x['nickname'] or x['user_id']} - {x['points']} 分")
            text = "\n".join(lines)
        await ctx.send_group(event.group_id, text)
        return True

    for _kw in sorted(RANK_KW - {"签到排行"}):
        def _mk_rank(kw):
            async def _h(event, cmd, _kw=kw):
                return await on_rank(event, cmd)
            return _h
        ctx.command(_kw, "积分排行")(_mk_rank(_kw))

    @ctx.command("我的积分", "查看自己的签到数据")
    async def on_me(event, cmd):
        if cmd not in ME_KW:
            return False
        r = do_me(event.group_id, event.user_id)
        if not r.get("ok"):
            await ctx.send_group(event.group_id, r.get("error", "查询失败"))
        else:
            # 格式跟原来内置的一模一样，用户习惯不能变
            await ctx.send_group(event.group_id,
                                 f"你的签到数据：\n"
                                 f"累计积分：{r['points']} 分\n"
                                 f"签到次数：{r['sign_count']} 次\n"
                                 f"连续天数：{r['streak']} 天\n"
                                 f"最长连续：{r['best_streak']} 天")
        return True

    @ctx.command("我的签到", "查看自己的签到数据")
    async def on_me2(event, cmd):
        return await on_me(event, cmd)

    @ctx.command("清空积分", "【超管】清空本群签到积分")
    async def on_clear(event, cmd):
        if cmd not in CLEAR_KW:
            return False
        ctx.sign_clear_group(event.group_id)
        ctx.log(f"{event.user_id} 清空本群积分 @群{event.group_id}")
        await ctx.send_group(event.group_id, "已清空本群签到积分")
        return True

    @ctx.command("清积分", "【超管】清空本群签到积分")
    async def on_clear2(event, cmd):
        return await on_clear(event, cmd)

    # ---------------- 管理接口 ----------------
    async def _api_overview(request, body):
        groups = []
        for g in ctx.sign_rank_all():
            gid = g.get("group_id")
            rank = ctx.sign_rank(gid, limit=50)
            groups.append({
                "group_id": gid, "count": g.get("cnt", 0),
                "total": g.get("total") or 0,
                "rank": [{"user_id": r.get("user_id"), "nickname": r.get("nickname"),
                          "points": r.get("points"), "sign_count": r.get("sign_count"),
                          "streak": r.get("streak")} for r in rank],
            })
        return {"groups": groups}

    async def _api_clear(request, body):
        gid = request.query_params.get("group_id") or (body or {}).get("group_id") or 0
        if not gid:
            return {"ok": False, "error": "缺少 group_id"}
        ctx.sign_clear_group(gid)
        ctx.log(f"清空群 {gid} 的积分（管理界面）")
        return {"ok": True}

    async def _api_do(request, body):
        q = request.query_params
        b = body or {}
        gid = q.get("group_id") or b.get("group_id")
        uid = q.get("user_id") or b.get("user_id")
        if not gid or not uid:
            return {"ok": False, "error": "缺少 group_id 或 user_id"}
        return await do_sign(gid, uid, str(q.get("nickname") or b.get("nickname") or ""))

    async def _api_rank(request, body):
        q = request.query_params
        b = body or {}
        gid = q.get("group_id") or b.get("group_id")
        if not gid:
            return {"ok": False, "error": "缺少 group_id"}
        try:
            lim = int(q.get("limit") or b.get("limit") or 10)
        except Exception:
            lim = 10
        return do_rank(gid, lim)

    async def _api_me(request, body):
        q = request.query_params
        b = body or {}
        gid = q.get("group_id") or b.get("group_id")
        uid = q.get("user_id") or b.get("user_id")
        if not gid or not uid:
            return {"ok": False, "error": "缺少 group_id 或 user_id"}
        return do_me(gid, uid)

    ctx.register_api("/admin/api/plugins/sign/overview", _api_overview, methods=("GET",))
    ctx.register_api("/admin/api/plugins/sign/clear", _api_clear, methods=("GET", "POST"))
    ctx.register_api("/admin/api/plugins/sign/do", _api_do, methods=("GET", "POST"))
    ctx.register_api("/admin/api/plugins/sign/rank", _api_rank, methods=("GET", "POST"))
    ctx.register_api("/admin/api/plugins/sign/me", _api_me, methods=("GET", "POST"))

    # ---------------- 配置项 ----------------
    ctx.register_config("签到积分", [
        ["sign_enabled", "签到功能", "bool"],
        ["sign_base_points", "签到基础分", "number"],
        ["sign_streak_bonus", "连续签到每日奖励", "number"],
        ["sign_max_bonus", "连续奖励上限", "number"],
    ])

    # ---------------- 插件自带页面 ----------------
    ctx.register_page("points", "签到积分", r"""
<div class="card">
  <div class="card-h"><h3>签到积分</h3><span class="en">Sign-in & Points</span>
    <div class="right"><button class="btn ghost sm" onclick="signRefresh()">刷新</button></div>
  </div>
  <div id="signContent"><div class="empty"><div class="big">…</div><div class="msg">加载中</div></div></div>
</div>

<div class="card" style="margin-top:16px">
  <div class="card-h"><h3>接口测试</h3><span class="en">Test</span></div>
  <div class="form-grid">
    <div class="form-row"><label>群号</label><input id="sg-gid" class="inp" placeholder="例如 100000000"></div>
    <div class="form-row"><label>QQ号</label><input id="sg-uid" class="inp" placeholder="例如 10001"></div>
  </div>
  <div style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap">
    <button class="btn primary" onclick="signDo()">执行签到</button>
    <button class="btn" onclick="signRank()">看排行</button>
    <button class="btn" onclick="signMe()">查我的积分</button>
  </div>
  <div class="api-result" id="sg-res">等待请求…</div>
</div>

<script>
async function signRefresh(){
  var el=document.getElementById("signContent");
  if(!el)return;
  el.innerHTML='<div class="empty"><div class="big">…</div><div class="msg">加载中</div></div>';
  var data=await api("/admin/api/plugins/sign/overview");
  if(!data||!data.groups||!data.groups.length){
    el.innerHTML='<div class="empty"><div class="msg">还没有人签到</div></div>';
    return;
  }
  var h="";
  for(var i=0;i<data.groups.length;i++){
    var g=data.groups[i];
    h+='<div class="group-block"><div class="group-head"><span>群 <span class="gid">'
      +g.group_id+'</span> · '+g.count+' 人 · 共 '+g.total+' 分</span>'
      +'<button class="btn danger sm" onclick="signClear('+g.group_id+')">清空</button></div>';
    h+='<table class="tbl"><thead><tr><th style="width:44px">#</th><th>QQ</th><th>昵称</th>'
      +'<th style="text-align:right">积分</th><th style="text-align:right">次数</th>'
      +'<th style="text-align:right">连续</th></tr></thead><tbody>';
    for(var j=0;j<g.rank.length;j++){
      var r=g.rank[j];
      h+='<tr><td class="rank">'+(j+1)+'</td><td class="mono">'+r.user_id+'</td>'
        +'<td class="strong">'+esc(r.nickname||"-")+'</td>'
        +'<td class="points">'+r.points+'</td>'
        +'<td style="text-align:right">'+r.sign_count+'</td>'
        +'<td style="text-align:right">'+r.streak+'</td></tr>';
    }
    h+='</tbody></table></div>';
  }
  el.innerHTML=h;
}

async function signClear(gid){
  if(!confirm("清空该群积分？不可恢复。"))return;
  await api("/admin/api/plugins/sign/clear?group_id="+gid,{method:"POST"});
  toast("已清空");
  signRefresh();
}

function signBox(v){var b=document.getElementById("sg-res");if(b)b.textContent=v;}
async function signDo(){
  var g=document.getElementById("sg-gid"),u=document.getElementById("sg-uid");
  if(!g.value||!u.value){signBox("请填群号和 QQ 号");return;}
  signBox("请求中…");
  var r=await api("/admin/api/plugins/sign/do?group_id="+encodeURIComponent(g.value)
                  +"&user_id="+encodeURIComponent(u.value));
  signBox((r&&r.message)||JSON.stringify(r,null,2));
  signRefresh();
}
async function signRank(){
  var g=document.getElementById("sg-gid");
  if(!g.value){signBox("请填群号");return;}
  signBox("请求中…");
  var r=await api("/admin/api/plugins/sign/rank?group_id="+encodeURIComponent(g.value)+"&limit=10");
  signBox(JSON.stringify(r,null,2));
}
async function signMe(){
  var g=document.getElementById("sg-gid"),u=document.getElementById("sg-uid");
  if(!g.value||!u.value){signBox("请填群号和 QQ 号");return;}
  signBox("请求中…");
  var r=await api("/admin/api/plugins/sign/me?group_id="+encodeURIComponent(g.value)
                  +"&user_id="+encodeURIComponent(u.value));
  signBox(JSON.stringify(r,null,2));
}

// 脚本注入时就把列表拉出来 —— 原来内置版是靠 setPage 里
// `if(n==="points")loadPoints()` 触发的，插件版得自己来，否则一直显示"加载中"。
signRefresh();
</script>
""")

    ctx.log("签到插件已就绪（命令：签到 / 打卡 / 签到排行 / 我的积分 …）")
