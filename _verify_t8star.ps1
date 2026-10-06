<#
  T8star-Aix · IndexTTS 2.5 安装自检
  检查运行时、依赖、模型、设置、服务状态，并可选做一次真实合成。

  用法：
    powershell -NoProfile -ExecutionPolicy Bypass -File .\_verify_t8star.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File .\_verify_t8star.ps1 -SkipSynth
#>
[CmdletBinding()]
param(
  [string]$InstallDir = "D:\ai\T8star-Aix-IndexTTS",
  [switch]$SkipSynth
)

$ErrorActionPreference = "Continue"
$pass = 0
$fail = 0
function Check([string]$name, [bool]$ok, [string]$extra = "") {
  if ($ok) { $script:pass++; Write-Host ("  [OK]   " + $name + $(if ($extra) { "  $extra" } else { "" })) -ForegroundColor Green }
  else { $script:fail++; Write-Host ("  [FAIL] " + $name + $(if ($extra) { "  $extra" } else { "" })) -ForegroundColor Red }
}
function Step([string]$m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }

$Res = Join-Path $InstallDir "resources"
$PyDir = Join-Path $Res "cpython-3.10.20-windows-x86_64-none"
$Py = Join-Path $PyDir "python.exe"
$ModelDir = Join-Path $InstallDir "IndexTTS-2.5"
$AppData = Join-Path $env:APPDATA "T8star-Aix · IndexTTS 2.5"

Step "目录结构"
Check "安装目录" (Test-Path $InstallDir) $InstallDir
Check "resources" (Test-Path $Res)
Check "内置 Python" (Test-Path $Py)
Check "site-packages" (Test-Path (Join-Path $Res "site-packages"))
Check "模型目录" (Test-Path $ModelDir)
foreach ($f in @("启动API服务.cmd", "停止API服务.cmd", "查看API服务状态.cmd")) {
  Check $f (Test-Path (Join-Path $InstallDir $f))
}

Step "Python 运行时"
if (Test-Path $Py) {
  $ver = & $Py -V 2>&1
  Check "解释器可用" ($LASTEXITCODE -eq 0) ($ver -join " ")
  $probe = @'
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
out = {}
try:
    import torch
    out["torch"] = torch.__version__
    out["cuda_build"] = torch.version.cuda
    out["cuda_ok"] = bool(torch.cuda.is_available())
    if out["cuda_ok"]:
        out["gpu"] = torch.cuda.get_device_name(0)
        out["cap"] = "%d.%d" % torch.cuda.get_device_capability(0)
except Exception as e:
    out["torch_error"] = "%s: %s" % (type(e).__name__, e)
for name in ("torchaudio", "numpy", "transformers", "gradio", "librosa", "fastapi",
             "uvicorn", "soundfile", "whisper", "modelscope", "audiotools", "fugashi",
             "wetext", "omegaconf", "sentencepiece", "av", "onnxruntime", "huggingface_hub"):
    try:
        mod = __import__(name)
        out[name] = getattr(mod, "__version__", "ok")
    except Exception as e:
        out[name] = "MISSING"
print(json.dumps(out, ensure_ascii=False))
'@
  $tmp = Join-Path $env:TEMP "_t8_verify_probe.py"
  Set-Content -LiteralPath $tmp -Value $probe -Encoding UTF8
  $json = & $Py $tmp 2>$null
  Remove-Item $tmp -Force -ErrorAction SilentlyContinue
  try {
    $info = $json | Select-Object -Last 1 | ConvertFrom-Json
    Check "torch" ($info.torch -ne $null) $info.torch
    Check "CUDA 可用" ($info.cuda_ok -eq $true) ("build " + $info.cuda_build + " | " + $info.gpu + " | sm" + $info.cap)
    foreach ($k in @("torchaudio", "numpy", "transformers", "gradio", "librosa", "fastapi",
                     "uvicorn", "soundfile", "whisper", "modelscope", "audiotools", "fugashi",
                     "wetext", "omegaconf", "sentencepiece", "av", "onnxruntime", "huggingface_hub")) {
      $v = $info.$k
      Check $k ($v -ne "MISSING") $v
    }
  } catch {
    Check "依赖自检" $false "无法解析探测输出"
  }
}

Step "模型文件（按清单抽取关键项）"
$manifest = Join-Path $Res "desktop_model_manifest.json"
if (Test-Path $manifest) {
  $m = Get-Content $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
  $missing = 0
  foreach ($rel in $m.files.PSObject.Properties.Name) {
    $p = Join-Path $ModelDir ($rel -replace "/", "\")
    if (-not (Test-Path $p) -or (Get-Item $p).Length -ne $m.files.$rel.size) {
      Write-Host ("         缺失或大小不符: " + $rel) -ForegroundColor Yellow
      $missing++
    }
  }
  Check ("全部 " + $m.files.PSObject.Properties.Name.Count + " 个模型文件大小正确") ($missing -eq 0) $(if ($missing) { "$missing 个有问题" } else { "" })
  $totalGB = [math]::Round((Get-ChildItem $ModelDir -Recurse -File | Measure-Object Length -Sum).Sum / 1GB, 2)
  Check "模型总大小" $true "$totalGB GB"
} else {
  Check "模型清单" $false $manifest
}

Step "配置"
Check "settings.json 存在" (Test-Path (Join-Path $AppData "settings.json"))
if (Test-Path (Join-Path $AppData "settings.json")) {
  $bytes = [System.IO.File]::ReadAllBytes((Join-Path $AppData "settings.json"))
  $hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191)
  Check "settings.json 无 BOM" (-not $hasBom)
  try {
    $s = Get-Content (Join-Path $AppData "settings.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    Check "modelDir 指向存在的目录" (Test-Path $s.modelDir) $s.modelDir
    Check "apiKey 已生成" ($s.apiKey.Length -ge 16) ("长度 " + $s.apiKey.Length)
    Check "apiHost 合法" (@("127.0.0.1", "0.0.0.0") -contains $s.apiHost) $s.apiHost
  } catch { Check "settings.json 可解析" $false }
}

Step "服务状态"
$listen = @(Get-NetTCPConnection -LocalPort 7861 -State Listen -ErrorAction SilentlyContinue).Count
Check "端口 7861 正在监听" ($listen -gt 0) $(if ($listen -eq 0) { "（服务未启动，先双击 启动API服务.cmd）" } else { "" })

if ($listen -gt 0 -and -not $SkipSynth) {
  Step "真实合成测试（约 30-60 秒）"
  $e2e = Join-Path $InstallDir "_download\_e2e_tts.py"
  if (Test-Path $e2e) {
    $out = & $Py $e2e 2>$null
    $okCount = ([regex]::Matches(($out -join "`n"), "HTTP 200")).Count
    Check "合成接口返回 200" ($okCount -ge 1) "$okCount 次成功"
    if ($out -match "已保存:\s*(\S+)") {
      $wav = $Matches[1]
      Check "生成文件存在" (Test-Path $wav) $wav
    }
  } else {
    Write-Host "  跳过（找不到 _e2e_tts.py）" -ForegroundColor Yellow
  }
}

Write-Host ""
Write-Host ("=" * 56)
Write-Host ("通过 $pass 项，失败 $fail 项") -ForegroundColor $(if ($fail) { "Red" } else { "Green" })
if ($fail) {
  Write-Host "有失败项。启动方式：$InstallDir\启动API服务.cmd" -ForegroundColor Yellow
} else {
  Write-Host "一切正常。启动方式：$InstallDir\启动API服务.cmd" -ForegroundColor Green
  Write-Host "接口文档：http://127.0.0.1:7861/docs" -ForegroundColor Green
}
Write-Host ("=" * 56)
