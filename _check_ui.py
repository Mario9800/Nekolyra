# -*- coding: utf-8 -*-
r"""管理页 UI 双重检查：① JS 语法 ② 模拟 DOM 执行并调用关键函数。

以后每次改管理页都跑这个，避免再出现"点不动"。

除了"函数不报错"，还断言三件容易踩的事：
  A. 插件页必须挂进 .content（挂到 main 上会被 flex:1 的 .content 顶下去 → 顶部一大块空白）
  B. 插件配置分组必须渲染进 cfgForm
  C. 插件页的 <script> 必须被重新创建过（innerHTML 插的 script 不会执行）
"""
import io
import os
import re
import subprocess
import sys
import tempfile

P = r"D:\ai\qq_bot\bot.py"
s = io.open(P, encoding="utf-8").read()
# 只在管理页 HTML（ADMIN_HTML）里抓 <script>。
# 整个 bot.py 里抓的话，代码注释/文档字符串里出现的 "<script>" 字样
# 会把提取范围整个带偏，抓出一大坨假 JS（这个坑真踩过，整个检查器都失效了）。
_m = re.search(r'^ADMIN_HTML\s*=\s*r"""(.*?)"""', s, re.S | re.M)
html = _m.group(1) if _m else s
blocks = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
if not blocks:
    sys.exit("在 ADMIN_HTML 里找不到 script 块")
js = max(blocks, key=len)
print(f"  JS {len(js)} 字符（取自 ADMIN_HTML）")

ok = True

# ---------- ① 语法 ----------
tmp = os.path.join(tempfile.gettempdir(), "_uichk.js")
io.open(tmp, "w", encoding="utf-8").write(js)
r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True,
                   errors="replace")
if r.returncode == 0:
    print("  [语法] ✅ 通过")
else:
    ok = False
    print("  [语法] ❌ 失败")
    for line in ((r.stdout or "") + (r.stderr or "")).splitlines()[:12]:
        print("      " + line[:180])

# ---------- ② 运行时 ----------
HARNESS = r"""
const store = {};
function mkEl(id){
  const el = { id, style:new Proxy({},{get:(t,k)=>(k in t?t[k]:""),set:(t,k,v)=>{t[k]=v;return true}}),
    dataset:{}, innerHTML:"", textContent:"", value:"", options:[], checked:false,
    classList:{_s:new Set(),
      toggle(c,f){ if(f===undefined){this._s.has(c)?this._s.delete(c):this._s.add(c);} else {f?this._s.add(c):this._s.delete(c);} return true;},
      add(c){this._s.add(c);}, remove(c){this._s.delete(c);}, contains(c){return this._s.has(c);}},
    _kids:[], _scripts:[],
    appendChild(c){ this._kids.push(c); return c; },
    removeChild(){}, addEventListener(){}, removeEventListener(){},
    querySelector(){return null},
    querySelectorAll(sel){ if(sel==="script") return this._scripts; return []; },
    getAttribute(){return null}, setAttribute(){}, focus(){}, click(){},
    insertAdjacentHTML(){}, closest(){return null}, parentNode:null,
    // 插件被卸载时 loadPluginPages 会调 .remove() 把它摘掉
    remove(){
      const i=PAGES.indexOf(this); if(i>=0)PAGES.splice(i,1);
      const j=NAV._kids.indexOf(this); if(j>=0)NAV._kids.splice(j,1);
      const k=CONTENT._kids.indexOf(this); if(k>=0)CONTENT._kids.splice(k,1);
    }
  };
  return el;
}

// 真实的容器元素，用来断言插件页挂哪了
const NAV = mkEl("nav.nav");
const CONTENT = mkEl("content");
const MAINONLY = mkEl("main");
const PAGES = [];

global.document = {
  getElementById(id){
    // 注意：page-* 不能自动创建！loadPluginPages 里有
    // "if(document.getElementById(pid))continue;" 的判断，
    // 自动创建会让它永远为真，插件页一个都建不出来。
    // 但要在已经创建出来的页面里找 —— 真实 DOM 就是这样，
    // 否则每次调用都新建一个页面，卸载后也清不干净。
    if(String(id).indexOf("page-")===0){
      if(store[id]) return store[id];
      const f = (typeof PAGES!=="undefined") ? PAGES.find(function(e){return e.id===id;}) : null;
      return f || null;
    }
    if(!store[id]) store[id]=mkEl(id);
    return store[id];
  },
  querySelector(sel){
    if(sel==="nav.nav") return NAV;
    if(sel==="main.main .content") return CONTENT;
    if(sel==="main.main") return MAINONLY;
    return null;
  },
  querySelectorAll(sel){
    if(sel===".nav-item") return NAV._kids;
    if(sel===".page") return PAGES;
    if(sel===".api-tab") return [];
    return [];
  },
  createElement(tag){
    const e = mkEl("new-"+tag);
    // 记录被赋值的 innerHTML，并在其中找 <script>
    let _h = "";
    Object.defineProperty(e, "innerHTML", {
      get(){ return _h; },
      set(v){
        _h = String(v);
        const m = _h.match(/<script[\s\S]*?<\/script>/g) || [];
        e._scripts = m.map(()=>({textContent:"x", src:"", parentNode:{replaceChild(){}}}));
      }
    });
    if(tag==="div") PAGES.push(e);
    return e;
  },
  addEventListener(){}, removeEventListener(){},
  body:mkEl("body"), documentElement:mkEl("html")
};
const NAVS = NAV._kids;
global.window = {addEventListener(){}, location:{search:""}, matchMedia:()=>({matches:false,addEventListener(){}})};
global.location = {search:"", href:"", reload(){}};
global.navigator = {clipboard:{writeText:()=>Promise.resolve()}, userAgent:"node"};
global.localStorage = {getItem:()=>null, setItem(){}, removeItem(){}};

// 插件页接口返回什么由这个变量控制 —— 用来测「卸载插件后侧边栏要跟着消失」
global.MOCK_PAGES = [{id:"lottery",title:"抽奖",
  html:'<div class="card">抽奖</div><script>var x=1;<\/script>'}];

// fetch 按 URL 返回像样的假数据，这样 loadConfig / loadPluginPages 能真的跑出东西
global.fetch = (url) => {
  const u = String(url);
  let data = {};
  if(u.includes("/admin/api/plugins/schema")){
    data = [
      {title:"抽奖",plugin:"lottery",plugin_title:"抽奖",
       fields:[["lottery_enabled","抽奖功能","bool"],
               ["lottery_active_days","活跃窗口（天）","number"]]},
      {title:"签到积分",plugin:"sign",plugin_title:"签到积分",
       fields:[["sign_enabled","签到功能","bool"]]},
    ];
  } else if(u.includes("/admin/api/plugins/pages")){
    data = global.MOCK_PAGES || [];
  } else if(u.includes("/admin/api/config")){
    data = {admin_token_set:true};
  } else if(u.includes("/admin/api/plugins/market")){
    data = {items:[],count:0,source:""};
  } else if(u.includes("/admin/api/plugins")){
    data = {items:[],count:0,loaded:0};
  }
  return Promise.resolve({ok:true,status:200,json:()=>Promise.resolve(data)});
};
global.setInterval = () => 0;
global.setTimeout = () => 0;
global.clearTimeout = () => {};
global.requestAnimationFrame = () => 0;
global.alert = () => {}; global.confirm = () => false;
global.encodeURIComponent = encodeURIComponent;

const ERRS = [];
process.on("uncaughtException", e => ERRS.push(e.message));

__JS__

const CALLS = [
  ["setPage('overview')",  ()=>setPage('overview','概览')],
  ["setPage('mem')",       ()=>setPage('mem','长期记忆')],
  ["setMemView('group')",  ()=>setMemView('group')],
  ["loadMem()",            ()=>loadMem()],
  ["loadMemByGroup()",     ()=>loadMemByGroup()],
  ["setMemView('user')",   ()=>setMemView('user')],
  ["loadMemByUser()",      ()=>loadMemByUser()],
  ["toggleMemAdd()",       ()=>toggleMemAdd()],
  ["memScopeChanged()",    ()=>memScopeChanged()],
  ["submitMemAdd()",       ()=>submitMemAdd()],
  ["toggleRaw(g1,1,2)",    ()=>toggleRaw('g1',1,2)],
  ["renderRaw([])",        ()=>renderRaw([])],
  ["clearMemUser(1,2)",    ()=>clearMemUser(1,2)],
  ["clearMemUserAll(2)",   ()=>clearMemUserAll(2)],
  ["setPage('kb')",        ()=>setPage('kb','知识库')],
  ["setPage('cfg')",       ()=>setPage('cfg','配置')],
  ["setPage('debug')",     ()=>setPage('debug','调试')],
  // ---- 插件页 ----
  ["setPage('plugins')",   ()=>setPage('plugins','插件')],
  ["loadPlugins()",        ()=>loadPlugins()],
  ["renderPlugins([])",    ()=>renderPlugins([])],
  ["renderPlugins(样本)",  ()=>renderPlugins([
      {name:'hello',folder:'hello',display_name:'打招呼',version:'1.0.0',
       author:'Nekolyra',description:'示例',tags:['示例'],enabled:true,error:'',
       commands:['你好','插件测试'],hooks:0},
      {name:'bad',folder:'bad',display_name:'坏的',version:'0.1',author:'',
       description:'',tags:[],enabled:false,error:'导入失败：SyntaxError',
       commands:[],hooks:0},
      {name:'empty',folder:'empty',display_name:'空',version:'1.0',author:'',
       description:'',tags:[],enabled:true,error:'',commands:[],hooks:2},
   ])],
  ["togglePlugin('hello',0)", ()=>togglePlugin('hello',0)],
  ["reloadPlugins()",      ()=>reloadPlugins()],
  // ---- 插件市场 ----
  ["setPlugTab('market')",    ()=>setPlugTab('market')],
  ["setPlugTab('installed')", ()=>setPlugTab('installed')],
  ["loadMarket(0)",        ()=>loadMarket(0)],
  ["loadMarket(1)",        ()=>loadMarket(1)],
  ["renderMarket([])",     ()=>renderMarket([])],
  ["renderMarket(样本)",   ()=>renderMarket([
      {name:'hello',display_name:'打招呼',version:'1.0.1',author:'Nekolyra',
       description:'示例插件',tags:['示例'],repo:'https://github.com/x/y',
       verified:true,installed:false,updatable:false,load_error:''},
      {name:'old',display_name:'旧的',version:'2.0.0',author:'a',
       description:'',tags:[],repo:'https://github.com/x/z',
       installed:true,installed_version:'1.0.0',updatable:true,load_error:''},
      {name:'broken',display_name:'坏的',version:'1.0',author:'',
       description:'',tags:[],repo:'',installed:true,
       installed_version:'1.0',updatable:false,load_error:'导入失败：SyntaxError'},
   ])],
  ["installPlugin('hello')",   ()=>installPlugin('hello')],
  ["installFromUrl()",     ()=>installFromUrl()],
  ["uninstallPlugin('hello')", ()=>uninstallPlugin('hello')],
  ["updatePlugin('hello')",    ()=>updatePlugin('hello')],
];
let fail=0;
for(const [n,f] of CALLS){
  try{ f(); console.log("  OK   "+n); }
  catch(e){ fail++; console.log("  FAIL "+n+"  ->  "+e.name+": "+e.message); }
}

// ---------- 断言 ----------
function chk(name, cond, extra){
  console.log((cond?"  PASS ":"  ASSERT-FAIL ")+name+(extra?"  ["+extra+"]":""));
  if(!cond) fail++;
}

(async function(){
 try{
  // A. 插件页必须挂进 .content
  // 注意：页面脚本自己在初始化时已经调过一次 loadPluginPages()，所以这里只
  // 断言"有且只在 .content 里"，并按 id 去重，不数绝对个数。
  try{ await loadPluginPages(); }catch(e){ console.log("  FAIL loadPluginPages "+e.message); fail++; }
  const inContent = CONTENT._kids.filter(function(k){return k && k.id==="page-lottery";});
  const inMain = MAINONLY._kids.filter(function(k){return k && k.id==="page-lottery";});
  const inNav = NAV._kids.filter(function(k){return k && k.dataset && k.dataset.page==="lottery";});
  chk("插件页挂进 .content", inContent.length>=1, "content里 page-lottery="+inContent.length);
  chk("插件页没挂到 main 上", inMain.length===0, "main里="+inMain.length);
  chk("插件导航项加进 nav", inNav.length>=1, "nav里="+inNav.length);
  // 导航项必须在，但要隐藏 —— 插件页面不占侧边栏（用户要求），
  // 可 setPage() 又要靠 .nav-item[data-page] 找页面，所以留着但藏起来
  const navCls = (inNav[0] && inNav[0].className) || "";
  chk("插件导航项是隐藏的（不占侧边栏）", navCls.indexOf("plug-nav-hidden")>=0,
      "class="+navCls);
  const pg0 = inContent[0] || {};
  chk("插件页顶部有返回条",
      (pg0.innerHTML||"").indexOf("plug-page-bar")>=0 &&
      (pg0.innerHTML||"").indexOf("返回插件")>=0, "");
  const pg = inContent[0] || {};
  chk("插件页 id 是 page-lottery", pg.id==="page-lottery", String(pg.id));
  chk("插件页的 <script> 被抓到", (pg._scripts||[]).length===1, "scripts="+((pg._scripts||[]).length));
  const nb = inNav[0] || {};
  chk("导航项 data-page=lottery", nb.dataset && nb.dataset.page==="lottery", String(nb.dataset&&nb.dataset.page));

  // B. 系统配置页只放内置分组，插件配置不该混进来
  try{
    await loadConfig();
  }catch(e){ console.log("  FAIL loadConfig "+e.message); fail++; }
  const form = document.getElementById("cfgForm");
  const html = (form && form.innerHTML) || "";
  chk("配置页渲染出内置分组", html.length>0 && html.indexOf("AI 后端")>=0,
      "cfgForm "+html.length+" 字符");
  chk("配置页没有插件分组（抽奖）", html.indexOf("lottery_enabled")<0,
      "插件配置应走独立小窗");
  chk("配置页没有插件分组（签到）", html.indexOf("sign_enabled")<0, "");

  // C. 每个插件一个独立配置小窗
  chk("configFor() 存在", typeof configFor==="function");
  chk("closePlugCfg() 存在", typeof closePlugCfg==="function");
  chk("savePlugCfg() 存在", typeof savePlugCfg==="function");
  const mask = document.getElementById("plugCfgMask");
  const mbody = document.getElementById("plugCfgBody");
  mask.hidden = true;
  try{ await configFor("lottery"); }catch(e){ console.log("  FAIL configFor "+e.message); fail++; }
  chk("打开插件配置后小窗出现", mask.hidden === false, "hidden="+mask.hidden);
  chk("小窗里渲染出该插件的配置项",
      (mbody.innerHTML||"").indexOf("lottery_enabled")>=0,
      (mbody.innerHTML||"").length+" 字符");
  chk("小窗标题带插件名",
      (document.getElementById("plugCfgTitle")||{}).textContent==="抽奖 · 配置",
      String((document.getElementById("plugCfgTitle")||{}).textContent));
  chk("只渲染该插件的项，不混入别的插件",
      (mbody.innerHTML||"").indexOf("sign_enabled")<0, "");
  closePlugCfg();
  chk("关闭后小窗隐藏", mask.hidden === true, "hidden="+mask.hidden);

  // C. 卸载插件后，它的页面和导航项必须跟着消失
  // （用户报过：插件删了侧边栏还留着，看着像没删掉）
  global.MOCK_PAGES = [{id:"lottery",title:"抽奖",html:'<div class="card">x</div>'}];
  try{ await loadPluginPages(); }catch(e){ console.log("  FAIL reload1 "+e.message); fail++; }
  const n1=NAV._kids.filter(function(k){return k.dataset&&k.dataset.pluginPage==="1";}).length;
  const p1=CONTENT._kids.filter(function(k){return k.dataset&&k.dataset.pluginPage==="1";}).length;
  global.MOCK_PAGES = [];   // 模拟插件被卸载：接口不再返回它
  try{ await loadPluginPages(); }catch(e){ console.log("  FAIL reload2 "+e.message); fail++; }
  const n2=NAV._kids.filter(function(k){return k.dataset&&k.dataset.pluginPage==="1";}).length;
  const p2=CONTENT._kids.filter(function(k){return k.dataset&&k.dataset.pluginPage==="1";}).length;
  chk("卸载后导航项消失", n2===0, "前 "+n1+" 后 "+n2);
  chk("卸载后插件页消失", p2===0, "前 "+p1+" 后 "+p2);

  // D. 按钮该在哪：市场只管"装/更新"，管理（停用/配置/卸载）全在「已安装」
  try{
    renderMarket([{name:'lottery',display_name:'抽奖',version:'1.0.0',
      enabled:true,installed:true,updatable:false,load_error:'',tags:[],repo:''}]);
  }catch(e){ console.log("  FAIL renderMarket "+e.message); fail++; }
  const mh=(document.getElementById("marketList")||{}).innerHTML||"";
  chk("市场卡片没有「卸载」", mh.indexOf("卸载")<0, "市场不该出现卸载键");
  chk("市场卡片没有「配置」", mh.indexOf("配置")<0, "");
  chk("市场卡片引导去「已安装」", mh.indexOf("已安装")>=0, "");
  chk("市场卡片没有「停用」", mh.indexOf("停用")<0, "");

  // 已安装页反过来：三个都要有
  try{
    renderPlugins([{name:'lottery',folder:'lottery',display_name:'抽奖',
      version:'1.0.0',author:'Nekolyra',description:'',tags:[],enabled:true,error:'',
      commands:['抽奖'],hooks:0,
      pages:['lottery'],page_titles:{lottery:'抽奖'},
      config:[{title:'抽奖',fields:[['lottery_enabled','抽奖功能','bool']]}]}]);
  }catch(e){ console.log("  FAIL renderPlugins "+e.message); fail++; }
  const ph=(document.getElementById("pluginList")||{}).innerHTML||"";
  chk("已安装卡片有「停用」", ph.indexOf("停用")>=0, "");
  chk("已安装卡片有「配置」", ph.indexOf("配置")>=0, "");
  chk("已安装卡片有「卸载」", ph.indexOf("卸载")>=0, "");
  chk("有页面的插件给「打开页面」", ph.indexOf("打开页面")>=0, "");
  chk("已安装卡片的配置按钮传插件名",
      ph.indexOf("configFor")>=0 && ph.indexOf("lottery")>=0, "");
  // 停用状态要显示「启用」
  try{
    renderPlugins([{name:'x',folder:'x',display_name:'X',version:'1.0.0',
      author:'',description:'',tags:[],enabled:false,error:'',commands:[],hooks:0,
      config:[]}]);
  }catch(e){}
  const ph2=(document.getElementById("pluginList")||{}).innerHTML||"";
  chk("已停用的显示「启用」", ph2.indexOf("启用")>=0, "");
  chk("没有配置分组的插件不给「配置」键", ph2.indexOf("configFor")<0, "");

  console.log("UNCAUGHT:" + ERRS.length + " " + ERRS.slice(0,3).join(" | "));
  console.log("FAILCOUNT:" + fail);
}catch(e){
  // 断言块自己抛异常的话，上面的 FAILCOUNT 根本不会打印 ——
  // 那会让 Python 侧误判成"全部通过"。这里必须显式喊出来。
  console.log("  FAIL 断言块异常中断: " + e.name + ": " + e.message);
  console.log("UNCAUGHT:" + ERRS.length + " " + ERRS.slice(0,3).join(" | "));
  console.log("FAILCOUNT:" + (fail + 1));
}
})();
"""
code = HARNESS.replace("__JS__", js)
tmp2 = os.path.join(tempfile.gettempdir(), "_uirun.js")
io.open(tmp2, "w", encoding="utf-8").write(code)

# harness 自己也要过语法检查 —— 之前只查了页面 JS，
# 结果 harness 少个括号导致断言块整个没跑，检查器却还报"通过"。
rs = subprocess.run(["node", "--check", tmp2], capture_output=True, text=True,
                    errors="replace")
if rs.returncode != 0:
    ok = False
    print("  [harness 语法] ❌ 失败")
    for line in ((rs.stdout or "") + (rs.stderr or "")).splitlines()[:8]:
        print("      " + line[:170])

r2 = subprocess.run(["node", tmp2], capture_output=True, text=True,
                    errors="replace", timeout=90)
out = ((r2.stdout or "") + (r2.stderr or "")).strip()
print("  [运行]")
fails = 0
asserts = 0
done = False
for line in out.splitlines():
    if line.startswith("FAILCOUNT:"):
        fails = int(line.split(":")[1])
        done = True
        continue
    if line.startswith("UNCAUGHT:"):
        n = int(line.split(":")[1].split()[0])
        if n:
            print("      " + line[:190])
        continue
    if "DBG" in line:
        print("    " + line.strip())
    elif "ASSERT-FAIL" in line or "FAIL " in line:
        print("    ❌ " + line.strip())
    elif line.strip().startswith("PASS"):
        asserts += 1
        print("    ✅ " + line.strip()[7:])
if fails:
    ok = False
# 没打出 FAILCOUNT 说明断言块根本没跑完 —— 以前这里会误报"通过"，
# 明明断言中断了却显示全绿。
if not done:
    ok = False
    print("    ❌ 断言块没有跑完（没输出 FAILCOUNT），检查器判定为失败")
print(f"  [断言] {asserts} 项通过")

if ok and fails == 0:
    print()
    print("  结果: ✅ UI 检查通过")
else:
    print()
    print("  结果: ❌ UI 检查失败")
    sys.exit(1)
