# -*- coding: utf-8 -*-
"""抽奖插件。

原来这一坨内置在主程序里（do_lottery + 命令分发 + /api/lottery + 调试面板），
现在整个搬进插件，主程序里一行都不剩。

装了这个插件，群里 @机器人 发「抽奖」就会从最近活跃成员里随机抽一个。
不需要了就在「插件」页停用或卸载。
"""
import random
import threading
import time


def setup(ctx):
    # 每个群的冷启动时间（内存里就够了，重启后重新计时）
    _last = {}
    _lock = threading.Lock()

    def _int(key, default):
        """配置值可能是字符串（界面上填的），统一转成整数。"""
        try:
            v = ctx.cfg(key, default)
            if v is None or v == "":
                return default
            return int(float(v))
        except Exception:
            return default

    def _bool(key, default):
        v = ctx.cfg(key, default)
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "on", "开启")
        return bool(v)

    async def draw(group_id):
        """抽一次奖。返回结果字典（ok / error）。"""
        if not _bool("lottery_enabled", True):
            return {"ok": False, "error": "抽奖功能已关闭"}

        gid = int(group_id)
        now = time.time()
        cd = _int("lottery_cooldown_minutes", 3) * 60

        with _lock:
            last = _last.get(gid, 0)
            if cd > 0 and now - last < cd:
                return {"ok": False, "error": "冷却中",
                        "wait_seconds": max(1, int(cd - (now - last)))}

            days = _int("lottery_active_days", 7)
            active = ctx.act_recent(gid, days=days) or []
            # 别把机器人自己抽出来
            me = ctx.self_id()
            if me:
                active = [a for a in active if str(a.get("user_id")) != me]
            if not active:
                return {"ok": False, "error": f"最近 {days} 天没有活跃成员"}

            winner = random.choice(active)
            _last[gid] = now

        uid = winner.get("user_id")
        nick = winner.get("nickname") or str(uid)
        ctx.log(f"群 {gid} 抽出 {nick}({uid})，候选 {len(active)} 人")
        return {
            "ok": True,
            "group_id": gid,
            "winner_id": uid,
            "winner_nick": nick,
            "candidate_count": len(active),
            "active_days": days,
            "message": f"恭喜 {nick}({uid}) 中奖！",
        }

    # ---------------- 群命令 ----------------
    @ctx.command("抽奖", "从最近活跃成员里随机抽一个")
    async def on_lottery(event, cmd):
        r = await draw(event.group_id)
        if r.get("ok"):
            # 用 mention 才能真的 @ 到人；也能直接跟字符串拼在列表里
            await ctx.send_group(event.group_id, [
                ctx.mention(r["winner_id"]),
                f" 恭喜中奖！\n从最近 {r['active_days']} 天的 "
                f"{r['candidate_count']} 位活跃成员中抽中了你。",
            ])
        else:
            text = r.get("error", "抽奖失败")
            if "wait_seconds" in r:
                text += f"（{r['wait_seconds']} 秒后再试）"
            await ctx.send_group(event.group_id, text)
        return True

    # ---------------- 管理接口 ----------------
    async def _api_draw(request, body):
        gid = request.query_params.get("group_id") or (body or {}).get("group_id") or 0
        if not gid:
            return {"ok": False, "error": "缺少 group_id"}
        try:
            return await draw(gid)
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    ctx.register_api("/admin/api/plugins/lottery/draw", _api_draw, methods=("GET", "POST"))

    # ---------------- 配置项 ----------------
    ctx.register_config("抽奖", [
        ["lottery_enabled", "抽奖功能", "bool"],
        ["lottery_active_days", "活跃窗口（天）", "number"],
        ["lottery_cooldown_minutes", "冷却时间（分钟）", "number"],
    ])

    # ---------------- 插件自带页面 ----------------
    ctx.register_page("lottery", "抽奖", """
<div class="card">
  <div class="card-h"><h3>抽奖</h3><span class="en">Lottery</span></div>
  <div class="page-desc">
    从最近活跃的群成员里随机抽一个。<br>
    群成员 @机器人 发「<b>抽奖</b>」就能抽；配置项在「系统配置 → 抽奖」里。<br>
    <span style="color:var(--text-3)">这个页面是插件自己加的 —— 插件可以往侧边栏加页面。</span>
  </div>
  <div class="form-grid">
    <div class="form-row"><label>群号</label>
      <input id="lot-gid" class="inp" placeholder="例如 100000000"></div>
  </div>
  <div style="margin-top:16px">
    <button class="btn primary" onclick="lotDraw()">抽一次</button>
  </div>
  <div class="api-result" id="lot-res">等待请求…</div>
</div>
<script>
async function lotDraw(){
  var el=document.getElementById("lot-gid");
  var box=document.getElementById("lot-res");
  var gid=(el&&el.value||"").trim();
  if(!gid){box.textContent="请先填群号";return;}
  box.textContent="抽奖中…";
  var r=await api("/admin/api/plugins/lottery/draw?group_id="+encodeURIComponent(gid));
  box.textContent=JSON.stringify(r,null,2);
}
</script>
""")

    ctx.log("抽奖插件已就绪（命令：抽奖）")
