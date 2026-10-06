<#
  bot 语音功能重启前后自检
  用法：
    powershell -NoProfile -ExecutionPolicy Bypass -File .\_check_voice_ready.ps1
#>
$ErrorActionPreference = "Continue"

function Line([string]$m, [string]$c = "Gray") { Write-Host $m -ForegroundColor $c }
function Head([string]$m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }

Head "1. bot 进程（应该只有 1 个，且启动时间晚于代码修改时间）"
$procs = Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -like "*qq_bot\bot.py*" }
if (-not $procs) {
  Line "  没有任何 bot.py 在运行 —— 请启动它" "Yellow"
} else {
  $codeTime = (Get-Item "D:\ai\qq_bot\bot.py").LastWriteTime
  foreach ($p in $procs) {
    $start = $p.CreationDate
    $fresh = $start -gt $codeTime
    $tag = if ($fresh) { "[OK 代码是新的]" } else { "[!! 跑的仍是旧代码，需重启]" }
    $color = if ($fresh) { "Green" } else { "Red" }
    Line ("  PID {0,-7} 启动 {1}  {2}" -f $p.ProcessId, $start, $tag) $color
  }
  if (@($procs).Count -gt 1) {
    Line ("  [!!] 有 " + @($procs).Count + " 个实例同时在跑，端口和数据库会互相抢，请只保留一个") "Red"
  }
}
Line ("  bot.py 代码修改时间: " + (Get-Item "D:\ai\qq_bot\bot.py").LastWriteTime)

Head "2. 端口占用"
foreach ($port in @(8080, 7861)) {
  $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  if ($c) {
    $proc = Get-Process -Id $c[0].OwningProcess -ErrorAction SilentlyContinue
    Line ("  端口 {0,-6} 监听中  (PID {1} {2})" -f $port, $c[0].OwningProcess, $proc.ProcessName) "Green"
  } else {
    $hint = if ($port -eq 7861) { "请双击 D:\ai\T8star-Aix-IndexTTS\启动API服务.cmd" } else { "请启动 bot.py" }
    Line ("  端口 {0,-6} 未监听  ->  {1}" -f $port, $hint) "Yellow"
  }
}

Head "3. 语音配置"
$cfg = Get-Content "D:\ai\qq_bot\config.json" -Raw -Encoding utf8 | ConvertFrom-Json
Line ("  voice_enabled  : " + $cfg.voice_enabled)
Line ("  voice_api_url  : " + $cfg.voice_api_url)
Line ("  voice_name     : " + $cfg.voice_name)
$keyOk = ($cfg.voice_api_key -and $cfg.voice_api_key.Length -ge 16)
Line ("  voice_api_key  : " + $(if ($keyOk) { "已设置（" + $cfg.voice_api_key.Length + " 字符）" } else { "未设置" })) $(if ($keyOk) { "Green" } else { "Red" })

Head "4. 直连 TTS 服务测试（绕开 bot，验证服务本身）"
if ($keyOk) {
  $py = "D:\ai\qq_bot\_voice_selftest.py"
  @'
import json, os, sys, time, urllib.request, urllib.error
cfg = json.load(open(r"D:\ai\qq_bot\config.json", encoding="utf-8"))
url = cfg["voice_api_url"]; key = cfg["voice_api_key"]; voice = cfg["voice_name"]
body = json.dumps({"model":"tts-1","input":"自检测试","voice":voice,
                   "response_format":"mp3","speed":1.0}).encode("utf-8")
req = urllib.request.Request(url, data=body, method="POST")
req.add_header("Authorization", "Bearer " + key)
req.add_header("Content-Type", "application/json")
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        blob = r.read()
        print("  [OK] HTTP %s  %d 字节  耗时 %.1fs  server=%s"
              % (r.status, len(blob), time.time()-t0, r.headers.get("server")))
except urllib.error.HTTPError as e:
    print("  [X]  HTTP %s  耗时 %.1fs  server=%s  body=%s"
          % (e.code, time.time()-t0, e.headers.get("server"), e.read()[:200]))
except Exception as e:
    print("  [X]  %s: %s  耗时 %.1fs" % (type(e).__name__, e, time.time()-t0))
'@ | Set-Content -LiteralPath $py -Encoding UTF8
  & "C:\Users\ry135\AppData\Local\Python\pythoncore-3.14-64\python.exe" $py
  Remove-Item $py -Force -ErrorAction SilentlyContinue
} else {
  Line "  跳过（未配置 API Key）" "Yellow"
}

Head "5. bot 日志里最近 8 条语音记录"
$log = "D:\ai\qq_bot\startup.log"
if (Test-Path $log) {
  $hits = Get-Content $log -Encoding utf8 | Select-String -Pattern "语音" | Select-Object -Last 8
  if ($hits) { $hits | ForEach-Object { Line ("  " + $_.Line.Trim()) } }
  else { Line "  （日志里还没有语音记录）" "Yellow" }
}

Write-Host ""
Line "下一步：浏览器打开 http://127.0.0.1:8080/admin?token=你的admin_token -> 语音合成 -> 点「生成测试」" "Cyan"
Line "如果失败，把上面第 5 节的新日志发我（现在会带耗时/地址/响应头，能直接定位）" "Cyan"
