<#
  T8star-Aix · IndexTTS 2.5 运行时自建 + 模型下载 + 校验
  目标：让 <InstallDir>\启动API服务.cmd 能用

  用法：
    pwsh -File .\_build_t8star_runtime.ps1                 # 全部执行
    pwsh -File .\_build_t8star_runtime.ps1 -SkipModel      # 只建运行时（跳过 8.3GB 模型）
    pwsh -File .\_build_t8star_runtime.ps1 -OnlyModel      # 只下模型
#>
[CmdletBinding()]
param(
  [string]$InstallDir = "D:\ai\T8star-Aix-IndexTTS",
  [string]$ModelDir = "D:\ai\T8star-Aix-IndexTTS\IndexTTS-2.5",
  [string]$PayloadDir = "D:\ai\qq_bot\desktop-app-update-v0.26.4-win32-x64\resources",
  [string]$PyVersion = "3.10.11",
  [switch]$SkipRuntime,
  [switch]$SkipModel,
  [switch]$OnlyModel
)

$ErrorActionPreference = "Continue"   # 原生命令（python/pip）往 stderr 写东西不该中断脚本，改用手动判 $LASTEXITCODE
$ProgressPreference = "SilentlyContinue"

function Step([string]$m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Ok([string]$m)   { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn([string]$m) { Write-Host "  [!]  $m" -ForegroundColor Yellow }
function Die([string]$m)  { Write-Host "  [X]  $m" -ForegroundColor Red; exit 1 }

$Res      = Join-Path $InstallDir "resources"
$PyDirName = "cpython-3.10.20-windows-x86_64-none"   # 目录名必须叫这个，脚本靠它找 python.exe
$PyDir    = Join-Path $Res $PyDirName
$SpDir    = Join-Path $Res "site-packages"
$DlDir    = Join-Path $InstallDir "_download"
$PyMirror = "https://pypi.tuna.tsinghua.edu.cn/simple"
$PyTorchIndex = "https://download.pytorch.org/whl/cu128"
$PipBootstrap = "https://pypi.tuna.tsinghua.edu.cn/pypi"   # 手动装 pip 时用（JSON API）

New-Item -ItemType Directory -Force -Path $Res, $SpDir, $DlDir, $ModelDir | Out-Null

# ============================================================
# 1. Python 运行时
# ============================================================
if (-not $OnlyModel -and -not $SkipRuntime) {
  Step "1/6 布置 Python $PyVersion 运行时"
  $pyExe = Join-Path $PyDir "python.exe"
  if (Test-Path $pyExe) {
    Ok "python.exe 已存在，跳过解压"
  } else {
    $zip = Join-Path $DlDir "python-$PyVersion-embed-amd64.zip"
    if (-not (Test-Path $zip)) {
      $url = "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-amd64.zip"
      Write-Host "  下载 $url"
      Invoke-WebRequest -Uri $url -OutFile $zip -TimeoutSec 300
    }
    Ok ("安装包 " + [math]::Round((Get-Item $zip).Length / 1MB, 1) + " MB")
    New-Item -ItemType Directory -Force -Path $PyDir | Out-Null
    Expand-Archive -LiteralPath $zip -DestinationPath $PyDir -Force
    Ok "已解压到 $PyDir"
  }

  # 打开 site-packages（嵌入式包默认关闭，pip 装的东西不会被 import）
  # 注意：._pth 里 # 开头的是注释，不能保留后再追加，否则会拼出垃圾行
  $pth = Get-ChildItem -LiteralPath $PyDir -Filter "python*._pth" | Select-Object -First 1
  if ($pth) {
    $wanted = @("python310.zip", ".", "..\site-packages", "import site")
    Set-Content -LiteralPath $pth.FullName -Value $wanted -Encoding ASCII
    Ok ("已重写 " + $pth.Name + "：" + ($wanted -join " | "))
  }

  # pip 引导：嵌入式发行版没有 ensurepip，bootstrap.pypa.io 的 /pip/3.10/get-pip.py 也已 404，
  # 所以直接用内置解释器抓 pip 的 wheel 解压进 site-packages（最稳）。
  & $pyExe -m pip --version > $null 2>&1
  if ($LASTEXITCODE -ne 0) {
    Write-Host "  嵌入式 Python 无 pip，改为直接解压 pip wheel..."
    $helper = Join-Path $PSScriptRoot "_bootstrap_pip.py"
    if (-not (Test-Path $helper)) { Die "缺少 $helper" }
    & $pyExe $helper $SpDir $PipBootstrap
  }
  & $pyExe -m pip --version
  if ($LASTEXITCODE -ne 0) { Die "pip 引导失败" }
  Ok "pip 可用"
}

$pyExe = Join-Path $PyDir "python.exe"
if (-not (Test-Path $pyExe)) { Die "找不到 $pyExe（先跑一次不带 -OnlyModel 的命令）" }

# ============================================================
# 2. torch 2.8.0 + cu128
# ============================================================
if (-not $OnlyModel -and -not $SkipRuntime) {
  Step "2/6 安装 torch 2.8.0+cu128（约 3GB，最慢的一步）"
  & $pyExe -c "import torch,sys;sys.exit(0 if torch.__version__.startswith('2.8.0') else 1)" 2>$null
  if ($LASTEXITCODE -eq 0) {
    Ok "torch 2.8.0 已安装"
  } else {
    & $pyExe -m pip install --no-warn-script-location `
      --index-url $PyTorchIndex `
      "torch==2.8.0" "torchaudio==2.8.0"
    if ($LASTEXITCODE -ne 0) { Die "torch 安装失败" }
    Ok "torch / torchaudio 安装完成"
  }
}

# ============================================================
# 3. 其余依赖
# ============================================================
if (-not $OnlyModel -and -not $SkipRuntime) {
  Step "3/6 安装其余依赖（走阿里云镜像）"
  $pkgs = @(
    "numpy<2.3",
    "scipy",
    "librosa",
    "soundfile",
    "einops",
    "transformers",
    "tokenizers",
    "safetensors",
    "huggingface_hub",
    "accelerate",
    "omegaconf",
    "munch",
    "json5",
    "pyyaml",
    "tqdm",
    "av",
    "pydub",
    "opencc-python-reimplemented",
    "sentencepiece",
    "tiktoken",
    "wetext",
    "fugashi",
    "requests",
    "psutil",
    "packaging",
    "matplotlib",
    "fastapi",
    "uvicorn[standard]",
    "pydantic",
    "python-multipart",
    "gradio",
    "onnxruntime"
  )
  & $pyExe -m pip install --no-warn-script-location -i $PyMirror @pkgs
  if ($LASTEXITCODE -ne 0) { Warn "部分依赖安装失败，先继续，稍后看报错" }
  else { Ok "依赖安装完成" }
}

# ============================================================
# 4. GPU 自检
# ============================================================
if (-not $OnlyModel -and -not $SkipRuntime) {
  Step "4/6 GPU 自检"
  $probe = @'
import sys
try:
    import torch
except Exception as e:
    print("IMPORT_FAIL", type(e).__name__, e); sys.exit(2)
print("torch", torch.__version__, "| cuda build", torch.version.cuda)
print("cuda available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device:", torch.cuda.get_device_name(0))
    print("capability:", torch.cuda.get_device_capability(0))
    try:
        x = torch.randn(512, 512, device="cuda")
        y = (x @ x).sum().item()
        print("matmul ok:", round(y, 2))
    except Exception as e:
        print("CUDA_RUNTIME_FAIL", type(e).__name__, e); sys.exit(3)
'@
  $probePath = Join-Path $DlDir "_gpu_probe.py"
  Set-Content -LiteralPath $probePath -Value $probe -Encoding UTF8
  & $pyExe $probePath
  if ($LASTEXITCODE -ne 0) { Warn "GPU 自检未通过（退出码 $LASTEXITCODE），仍继续后面的步骤" }
}

# ============================================================
# 5. 模型下载 + SHA-256 校验
# ============================================================
if (-not $SkipModel) {
  Step "5/6 下载 IndexTTS 2.5 模型（8.29GB，可断点续传，可反复重跑）"
  $dlScript = Join-Path $PSScriptRoot "_fetch_t8star_models.py"
  if (-not (Test-Path $dlScript)) { Die "缺少 $dlScript" }
  & $pyExe $dlScript --manifest (Join-Path $PayloadDir "desktop_model_manifest.json") --target $ModelDir
  if ($LASTEXITCODE -ne 0) { Warn "模型下载未全部完成，可重跑本脚本继续" }
  else { Ok "模型齐全并通过校验" }
}

# ============================================================
# 6. 预置 settings.json
# ============================================================
if (-not $OnlyModel) {
  Step "6/6 预置桌面设置（绕过'请先打开桌面程序'）"
  $appData = Join-Path $env:APPDATA "T8star-Aix · IndexTTS 2.5"
  New-Item -ItemType Directory -Force -Path $appData | Out-Null
  $settingsPath = Join-Path $appData "settings.json"
  $settings = [ordered]@{
    modelDir      = $ModelDir
    outputDir     = (Join-Path $InstallDir "outputs")
    dataDir       = $appData
    apiHost       = "127.0.0.1"
    apiPort       = 7861
    apiSaveHistory = $true
    apiDefaultVoice = ""
    accelerationMode = "off"
    precisionMode = "auto"
    referenceDevice = "auto"
  }
  if (Test-Path $settingsPath) {
    try {
      $old = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
      foreach ($p in $old.PSObject.Properties) {
        if (-not $settings.Contains($p.Name)) { $settings[$p.Name] = $p.Value }
      }
      Ok "已合并既有 settings.json"
    } catch { Warn "既有 settings.json 无法解析，将覆盖" }
  }
  ($settings | ConvertTo-Json -Depth 5) | Set-Content -LiteralPath $settingsPath -Encoding UTF8
  Ok "写入 $settingsPath"
  Write-Host ("    modelDir = " + $ModelDir)
}

Write-Host ""
Write-Host "完成。启动方式：" -ForegroundColor Cyan
Write-Host ("   " + (Join-Path $InstallDir "启动API服务.cmd"))
Write-Host ("   文档 http://127.0.0.1:7861/docs   健康检查 /health")
