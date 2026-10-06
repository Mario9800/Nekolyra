# -*- coding: utf-8 -*-
r"""广告拦截插件。

原来这是主程序里的内置功能（ad_guard / _on_ad / detect_ad / _ad_rule），
现在整个搬出来做成插件 —— 用到的那几样本事（撤回、踢人、拉黑、超管判断、
取昵称）已经加进 PluginContext，插件不用 import 主程序的内部函数。

配置键**沿用原来那几个**（ad_filter_enabled / ad_hard_keywords / ...），
所以老用户升级后原来的词表直接就生效，不用重新填。

兼容两种存法：
  · 列表        ["在线观看", "无限观看"]     （老配置就是这个）
  · 换行分隔串  "在线观看\n无限观看"          （插件页里编辑的就是这个）
读的时候两种都认。
"""
import re

# 网址匹配：http(s):// 开头，或者 www. 开头
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s，。；！？、）】]+", re.I)


def _as_list(v):
    r"""把配置值统一成字符串列表。列表和换行/逗号分隔串都认。"""
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        return [str(x).strip() for x in v if str(x).strip()]
    out = []
    for line in str(v).replace("，", "\n").replace(",", "\n").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def setup(ctx):
    # ---------------- 词表 ----------------
    def lists():
        hard = _as_list(ctx.cfg("ad_hard_keywords"))
        soft = _as_list(ctx.cfg("ad_soft_keywords"))
        doms = [d.lower() for d in _as_list(ctx.cfg("ad_domains"))]
        return hard, soft, doms

    def detect(text):
        r"""返回 (是否广告, 原因)。跟原来主程序里的 detect_ad 一致。"""
        if not ctx.cfg("ad_filter_enabled", True):
            return False, ""
        text = str(text or "")
        if not text:
            return False, ""
        hard, soft, doms = lists()
        lower = text.lower()

        for kw in hard:
            if kw in text or kw.lower() in lower:
                return True, "敏感词「%s」" % kw

        for d in doms:
            if d in lower:
                return True, "黑域名「%s」" % d

        urls = _URL_RE.findall(text)

        # 两条以上链接 + 长度过 60 —— 典型的小广告格式
        if len(urls) >= 2 and len(text) > 60:
            return True, "多条链接（%d 条）" % len(urls)

        # 一条链接 + 两个软词
        if urls:
            hits = [kw for kw in soft if kw in text or kw.lower() in lower]
            if len(hits) >= 2:
                return True, "链接+敏感词（%s）" % ",".join(hits[:3])

        return False, ""

    # ---------------- 消息钩子 ----------------
    @ctx.on_message()
    async def on_msg(event, text):
        r"""返回 True = 吞掉这条消息，后面的内置逻辑（含 AI）不再处理它。

        只处理群消息 —— 插件钩子是在群消息处理链里调用的，
        但保险起见还是判一下 group_id。
        """
        gid = getattr(event, "group_id", None)
        uid = getattr(event, "user_id", None)
        if gid is None or uid is None:
            return False
        if not ctx.cfg("ad_filter_enabled", True):
            return False

        # 白名单：群、人、超管三类都放行
        try:
            if int(gid) in {int(x) for x in _as_list(ctx.cfg("ad_whitelist_groups"))}:
                return False
        except Exception:
            pass
        if str(uid) in set(_as_list(ctx.cfg("ad_whitelist_users"))):
            return False
        if ctx.is_super(uid):
            return False

        ok, reason = detect(text)
        if not ok:
            return False

        nick = await ctx.nick(gid, uid)

        if not await ctx.delete_msg(event):
            ctx.warn("撤回失败（可能不是管理员，或消息太旧）")

        ctx.bl_add(uid, gid, nick, "发广告：%s" % reason)

        kicked = await ctx.kick(gid, uid, reject_add=True)
        ctx.log("%s(%s) @群%s 触发：%s｜踢出=%s"
                % (nick, uid, gid, reason, "OK" if kicked else "FAIL"))

        await ctx.send_group(gid, "检测到广告，已移除 %s（%s）。" % (nick, reason))

        # 私聊通知所有超管
        for admin in _as_list(ctx.cfg("superusers")):
            try:
                await ctx.send_private(int(admin), (
                    "广告拦截\n────────────\n"
                    "群号：%s\n"
                    "用户：%s（%s）\n"
                    "原因：%s\n"
                    "踢出：%s\n"
                    "原文：%s\n"
                    "────────────\n已加入黑名单"
                    % (gid, nick, uid, reason,
                       "成功" if kicked else "失败", str(text)[:200])))
            except Exception:
                pass

        return True        # 吞掉，不再走 AI

    # ---------------- 管理页 ----------------
    def _read_cfg():
        return {
            "enabled": bool(ctx.cfg("ad_filter_enabled", True)),
            "hard": "\n".join(_as_list(ctx.cfg("ad_hard_keywords"))),
            "soft": "\n".join(_as_list(ctx.cfg("ad_soft_keywords"))),
            "domains": "\n".join(_as_list(ctx.cfg("ad_domains"))),
            "wl_users": "\n".join(_as_list(ctx.cfg("ad_whitelist_users"))),
            "wl_groups": "\n".join(_as_list(ctx.cfg("ad_whitelist_groups"))),
        }

    def api_get(request, body):
        return {"ok": True, "cfg": _read_cfg()}

    def api_save(request, body):
        b = body or {}
        ctx.set_cfg("ad_filter_enabled", bool(b.get("enabled", True)))
        for key, field in (("ad_hard_keywords", "hard"),
                           ("ad_soft_keywords", "soft"),
                           ("ad_domains", "domains"),
                           ("ad_whitelist_users", "wl_users"),
                           ("ad_whitelist_groups", "wl_groups")):
            ctx.set_cfg(key, str(b.get(field, "") or "").strip())
        ctx.log("配置已更新")
        return {"ok": True}

    def api_test(request, body):
        text = str((body or {}).get("text", "") or "")
        ok, reason = detect(text)
        return {"ok": True, "hit": ok, "reason": reason or "没命中任何规则"}

    ctx.register_api("/admin/api/plugins/adfilter/get", api_get, ("GET",))
    ctx.register_api("/admin/api/plugins/adfilter/save", api_save, ("POST",))
    ctx.register_api("/admin/api/plugins/adfilter/test", api_test, ("POST",))

    PAGE = r"""
<div class="card">
  <div class="card-h"><h3>广告拦截</h3><span class="en">Ad Filter</span>
    <div class="right">
      <button class="btn sm" onclick="adfLoad()">重置</button>
      <button class="btn primary sm" onclick="adfSave()">保存</button>
    </div>
  </div>
  <div class="form-grid">
    <div class="form-row"><label>开启广告拦截</label>
      <select id="adf-enabled">
        <option value="1">开启</option><option value="0">关闭</option>
      </select></div>
  </div>
  <div class="form-row full" style="margin-top:6px"><label>高置信词（命中任意一个就秒踢，每行一个）</label>
    <textarea id="adf-hard" style="min-height:120px;width:100%"></textarea></div>
  <div class="form-row full"><label>软词（1 条链接 + 2 个软词才触发，每行一个）</label>
    <textarea id="adf-soft" style="min-height:100px;width:100%"></textarea></div>
  <div class="form-row full"><label>黑域名（每行一个，包含匹配）</label>
    <textarea id="adf-domains" style="min-height:80px;width:100%"></textarea></div>
  <div class="form-row full"><label>白名单 QQ（每行一个，可留空）</label>
    <textarea id="adf-wlu" style="min-height:70px;width:100%"></textarea></div>
  <div class="form-row full"><label>白名单群号（每行一个，可留空）</label>
    <textarea id="adf-wlg" style="min-height:70px;width:100%"></textarea></div>
  <div style="margin-top:18px;padding-top:16px;border-top:1px solid var(--line)">
    <div style="font-size:12px;color:var(--text-3);margin-bottom:10px">
      测试一段文本，看是否会被判定为广告：</div>
    <textarea id="adf-test" class="inp" placeholder="粘贴一段消息..." style="width:100%;min-height:76px"></textarea>
    <div style="margin-top:10px"><button class="btn" onclick="adfTest()">测试</button></div>
    <div class="api-result" id="adf-res" style="margin-top:12px">等待测试...</div>
  </div>
</div>
<script>
function adfPut(id,v){var e=document.getElementById(id);if(e)e.value=v;}
function adfGet(id){var e=document.getElementById(id);return e?e.value:"";}
async function adfLoad(){
  try{
    var r=await api("/admin/api/plugins/adfilter/get");
    if(!r||!r.ok){toast("读取失败","err");return;}
    var c=r.cfg||{};
    adfPut("adf-enabled", c.enabled?"1":"0");
    adfPut("adf-hard", c.hard||"");
    adfPut("adf-soft", c.soft||"");
    adfPut("adf-domains", c.domains||"");
    adfPut("adf-wlu", c.wl_users||"");
    adfPut("adf-wlg", c.wl_groups||"");
  }catch(e){toast("读取异常："+(e&&e.message||e),"err");}
}
async function adfSave(){
  var body={enabled:adfGet("adf-enabled")==="1",
    hard:adfGet("adf-hard"), soft:adfGet("adf-soft"),
    domains:adfGet("adf-domains"), wl_users:adfGet("adf-wlu"),
    wl_groups:adfGet("adf-wlg")};
  var r=await post("/admin/api/plugins/adfilter/save", body);
  if(!r||!r.ok){toast("保存失败："+((r&&r.error)||"无响应"),"err");return;}
  toast("已保存，立即生效");
}
async function adfTest(){
  var t=adfGet("adf-test");
  var box=document.getElementById("adf-res");
  if(!t.trim()){box.textContent="先粘贴一段文本";return;}
  var r=await post("/admin/api/plugins/adfilter/test",{text:t});
  if(!r||!r.ok){box.textContent="测试失败";return;}
  box.textContent=(r.hit?"会拦：":"不拦：")+r.reason;
}
window.addEventListener("load",function(){setTimeout(adfLoad,300);});
</script>
"""
    ctx.register_page("adfilter", "广告拦截", PAGE)
    ctx.log("已装载（消息钩子 + 管理页）")
