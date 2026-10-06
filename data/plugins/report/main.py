# -*- coding: utf-8 -*-
"""群报插件。

原来内置在主程序里（do_report + 十几个关键词 + /api/report + 调试面板），
现在整个搬进插件。

群里 @机器人 发「群报」「日报」「战报」看昨天，「今日群报」看今天。
"""
from datetime import date, timedelta

# 三类关键词，跟原来内置的一模一样（用户习惯不能变）
YESTERDAY_KW = {
    "昨日群报", "昨天群报", "昨日日报", "昨天日报",
    "昨日报告", "昨天报告", "昨日战报", "昨天战报",
    "昨日总结", "昨天总结",
}
TODAY_KW = {
    "今日群报", "今天群报", "今日日报", "今天日报",
    "今日报告", "今天报告", "今日战报", "今天战报",
    "今日总结", "今天总结",
}
DEFAULT_KW = {"群报", "日报", "群日报", "战报", "群总结", "群报告"}


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

    async def build(group_id, target="yesterday"):
        """生成群报。返回结果字典。"""
        if not _bool("report_enabled", True):
            return {"ok": False, "error": "群报功能已关闭"}

        gid = int(group_id)
        if target == "today":
            d = date.today()
            label = "今日"
        else:
            d = date.today() - timedelta(days=1)
            label = "昨日"
        ds = d.isoformat()

        summary = ctx.daily_summary(gid, ds)
        top_n = _int("report_top_n", 10)
        rank = ctx.daily_rank(gid, ds, limit=top_n)

        if summary.get("total", 0) == 0:
            return {
                "ok": True, "group_id": gid, "date": ds, "label": label,
                "users": 0, "total": 0, "rank": [],
                "message": f"{label}群报（{ds}）\n本群没有发言记录～",
            }

        lines = [f"{label}群报（{ds}）"]
        lines.append(f"共 {summary['users']} 人发言，{summary['total']} 条消息")
        if rank:
            lines.append(f"— 发言榜 TOP{len(rank)} —")
            for i, r in enumerate(rank):
                lines.append(f"{i+1}. {r.get('nickname') or r.get('user_id')}："
                             f"{r.get('msg_count')} 条")
        msg = "\n".join(lines)
        return {
            "ok": True, "group_id": gid, "date": ds, "label": label,
            "users": summary["users"], "total": summary["total"],
            "rank": [{"rank": i + 1, "user_id": r.get("user_id"),
                      "nickname": r.get("nickname"),
                      "msg_count": r.get("msg_count")}
                     for i, r in enumerate(rank)],
            "message": msg,
        }

    # ---------------- 群命令 ----------------
    @ctx.command("群报", "昨日发言排行（也认「日报 / 战报 / 群总结」）")
    async def on_default(event, cmd):
        if cmd not in DEFAULT_KW:
            return False
        r = await build(event.group_id, "yesterday")
        await ctx.send_group(event.group_id, r.get("message") or r.get("error", "群报生成失败"))
        return True

    @ctx.command("昨日群报", "昨日发言排行")
    async def on_yesterday(event, cmd):
        if cmd not in YESTERDAY_KW:
            return False
        r = await build(event.group_id, "yesterday")
        await ctx.send_group(event.group_id, r.get("message") or r.get("error", "群报生成失败"))
        return True

    @ctx.command("今日群报", "今日发言排行")
    async def on_today(event, cmd):
        if cmd not in TODAY_KW:
            return False
        r = await build(event.group_id, "today")
        await ctx.send_group(event.group_id, r.get("message") or r.get("error", "群报生成失败"))
        return True

    # 其余别名：命令注册是按「被 @ 的原文精确匹配」找 handler 的，
    # 所以要把每个别名都注册上，才能都走到上面三个逻辑里。
    for _kw in sorted((YESTERDAY_KW | TODAY_KW | DEFAULT_KW)
                      - {"群报", "昨日群报", "今日群报"}):
        def _make(kw):
            async def _h(event, cmd, _kw=kw):
                tgt = "today" if _kw in TODAY_KW else "yesterday"
                r = await build(event.group_id, tgt)
                await ctx.send_group(event.group_id,
                                     r.get("message") or r.get("error", "群报生成失败"))
                return True
            return _h
        ctx.command(_kw, "群报别名")(_make(_kw))

    # ---------------- 管理接口 ----------------
    async def _api_report(request, body):
        gid = request.query_params.get("group_id") or (body or {}).get("group_id") or 0
        tgt = request.query_params.get("target") or (body or {}).get("target") or "yesterday"
        if not gid:
            return {"ok": False, "error": "缺少 group_id"}
        try:
            return await build(gid, tgt)
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    ctx.register_api("/admin/api/plugins/report/gen", _api_report,
                     methods=("GET", "POST"))

    # ---------------- 配置项 ----------------
    ctx.register_config("群报", [
        ["report_enabled", "群报功能", "bool"],
        ["report_top_n", "显示前几名", "number"],
    ])

    # ---------------- 插件自带页面 ----------------
    ctx.register_page("report", "群报", """
<div class="card">
  <div class="card-h"><h3>群报</h3><span class="en">Daily Report</span></div>
  <div class="page-desc">
    统计群里某天的发言排行。群成员 @机器人 发「<b>群报</b>」「<b>日报</b>」「<b>战报</b>」看昨天，
    发「<b>今日群报</b>」看今天。<br>
    配置项在「系统配置 → 群报」里。
  </div>
  <div class="form-grid">
    <div class="form-row"><label>群号</label>
      <input id="rp-gid" class="inp" placeholder="例如 100000000"></div>
    <div class="form-row"><label>看哪天</label>
      <select id="rp-target" class="inp">
        <option value="yesterday">昨日</option>
        <option value="today">今日</option>
      </select></div>
  </div>
  <div style="margin-top:16px">
    <button class="btn primary" onclick="rpGen()">生成群报</button>
  </div>
  <div class="api-result" id="rp-res">等待请求…</div>
</div>
<script>
async function rpGen(){
  var g=document.getElementById("rp-gid");
  var t=document.getElementById("rp-target");
  var box=document.getElementById("rp-res");
  var gid=(g&&g.value||"").trim();
  if(!gid){box.textContent="请先填群号";return;}
  box.textContent="生成中…";
  var r=await api("/admin/api/plugins/report/gen?group_id="+encodeURIComponent(gid)
                  +"&target="+encodeURIComponent(t?t.value:"yesterday"));
  box.textContent=(r&&r.message)?r.message:JSON.stringify(r,null,2);
}
</script>
""")

    ctx.log("群报插件已就绪（命令：群报 / 日报 / 战报 / 今日群报 …）")
