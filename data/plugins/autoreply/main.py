# -*- coding: utf-8 -*-
"""关键词回复插件 —— 官方示例。

这个插件的代码**原样来自 nekoe-plugins 仓库 README 里的例子**，
存在的意义是：它能跑，就说明文档没写错。

演示了：自带数据表、消息钩子、配置项、命中统计、管理接口、插件自带页面。
"""
import time

TABLE = "autoreply_hits"
PAIR_SEP = "="


def setup(ctx):
    # 自己的表自己建
    ctx.db_exec(f"""CREATE TABLE IF NOT EXISTS {TABLE}(
        user_id INTEGER, group_id INTEGER, n INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, group_id))""")

    _last = {}

    def _bool(key, d):
        v = ctx.cfg(key, d)
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("true", "1", "yes", "on", "开启")

    def _int(key, d):
        try:
            return int(float(ctx.cfg(key, d)))
        except Exception:
            return d

    def pairs():
        """把配置里的 "关键词=回复" 多行文本解析成字典。"""
        out = {}
        raw = str(ctx.cfg("ar_pairs", "") or "")
        for line in raw.splitlines():
            line = line.strip()
            if not line or PAIR_SEP not in line:
                continue
            k, v = line.split(PAIR_SEP, 1)
            k, v = k.strip(), v.strip()
            if k and v:
                out[k] = v
        return out

    @ctx.on_message()
    async def on_msg(event, text):
        if not _bool("ar_enabled", True):
            return False
        if not getattr(event, "group_id", None):
            return False          # 只管群消息
        text = (text or "").strip()
        if not text:
            return False

        rules = pairs()
        hit = None
        for k in rules:
            if text == k:
                hit = k
                break
        if hit is None:
            return False

        # 冷却按 (群, 关键词) 各算各的 —— 按整群算的话，
        # 同一群里连发两个不同关键词，第二个会被误吞。
        cd = _int("ar_cooldown", 5)
        gid = int(event.group_id)
        now = time.time()
        ck = (gid, hit)
        if cd > 0 and now - _last.get(ck, 0) < cd:
            return True
        _last[ck] = now

        # 记一次命中
        try:
            ctx.db_exec(f"""INSERT INTO {TABLE}(user_id, group_id, n) VALUES(?,?,1)
                            ON CONFLICT(user_id,group_id) DO UPDATE SET n=n+1""",
                        (int(event.user_id), gid))
        except Exception as e:
            ctx.warn(f"记命中失败：{type(e).__name__}: {e}")

        await ctx.send_group(gid, rules[hit])
        ctx.log(f"群 {gid} 命中「{hit}」")
        return True

    # ---------------- 管理接口 ----------------
    async def _stats(request, body):
        rows = ctx.db_query(
            f"SELECT group_id, SUM(n) AS total, COUNT(*) AS users "
            f"FROM {TABLE} GROUP BY group_id ORDER BY total DESC")
        return {"ok": True, "groups": rows}

    async def _clear(request, body):
        gid = request.query_params.get("group_id") or (body or {}).get("group_id")
        if not gid:
            return {"ok": False, "error": "缺少 group_id"}
        n = ctx.db_exec(f"DELETE FROM {TABLE} WHERE group_id=?", (int(gid),))
        ctx.log(f"清空群 {gid} 的命中统计（{n} 行）")
        return {"ok": True, "deleted": n}

    async def _rules(request, body):
        r = pairs()
        return {"ok": True, "count": len(r), "rules": r}

    ctx.register_api("/admin/api/plugins/autoreply/stats", _stats, methods=("GET",))
    ctx.register_api("/admin/api/plugins/autoreply/clear", _clear,
                     methods=("GET", "POST"))
    ctx.register_api("/admin/api/plugins/autoreply/rules", _rules, methods=("GET",))

    # ---------------- 配置项（会出现在这个插件自己的配置小窗里）----------------
    ctx.register_config("关键词回复", [
        ["ar_enabled", "开启", "bool"],
        ["ar_pairs", "规则（每行一条：关键词=回复）", "text"],
        ["ar_cooldown", "同群冷却（秒）", "number"],
    ])

    # ---------------- 插件自带页面 ----------------
    ctx.register_page("autoreply", "关键词回复", """
<div class="card">
  <div class="card-h"><h3>关键词回复</h3><span class="en">Auto Reply</span></div>
  <div class="page-desc">
    规则在「插件 → 已安装 → 关键词回复 → 配置」里改，每行一条：<code>关键词=回复</code>。
  </div>
  <div class="form-grid">
    <div class="form-row"><label>群号</label>
      <input id="ar-gid" class="inp" placeholder="例如 100000000"></div>
  </div>
  <div style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap">
    <button class="btn primary" onclick="arStats()">看统计</button>
    <button class="btn" onclick="arRules()">看规则</button>
    <button class="btn danger" onclick="arClear()">清空本群</button>
  </div>
  <div class="api-result" id="ar-res">等待请求…</div>
</div>
<script>
// 脚本跑在全局作用域，函数名一定要加自己的前缀
async function arStats(){
  var b=document.getElementById("ar-res");
  b.textContent="查询中…";
  var r=await api("/admin/api/plugins/autoreply/stats");
  b.textContent=JSON.stringify(r,null,2);
}
async function arRules(){
  var b=document.getElementById("ar-res");
  var r=await api("/admin/api/plugins/autoreply/rules");
  b.textContent=JSON.stringify(r,null,2);
}
async function arClear(){
  var g=document.getElementById("ar-gid");
  var b=document.getElementById("ar-res");
  if(!g.value){b.textContent="请填群号";return;}
  var r=await api("/admin/api/plugins/autoreply/clear?group_id="
                  +encodeURIComponent(g.value),{method:"POST"});
  b.textContent=JSON.stringify(r,null,2);
  arStats();
}
</script>
""")

    ctx.log("关键词回复插件已就绪")
