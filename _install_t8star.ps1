<#
  T8star-Aix · IndexTTS 2.5 便携包下载 + 安装 + 增量更新 + 验证

  用法：
    # 1) 只下载（支持断点续传，可以反复重跑）
    pwsh -File .\_install_t8star.ps1 -Url "https://.../T8star-Aix-IndexTTS-2.5-v0.26.4-win32-x64.zip"

    # 2) 已经下载好了，只安装
    pwsh -File .\_install_t8star.ps1 -Package "E:\下载\T8star-Aix-IndexTTS-2.5-....zip"

    # 3) 指定安装目录 / 跳过增量覆盖 / 跳过模型下载
    pwsh -File .\_install_t8star.ps1 -Package <zip> -InstallDir "E:\T8star-Aix" -NoUpdate -NoModel

  做什么：
    - 下载（BITS 续传）或校验本地压缩包
    - 解压到安装目录，自动剥掉压缩包里的单层顶层目录
    - 用仓库里的 0.26.4 增量包覆盖（含 app.asar / indextts 源码）
    - 校验 resources\cpython-* 与 site-packages 是否齐全
    - 打印 API 服务启动方式与后续步骤
#>
[CmdletBinding()]
param(
  [string]$Url = "",
  [string]$Package = "",
  [string]$InstallDir = "D:\ai\T8star-Aix-IndexTTS",
  [string]$UpdateZip = "",
  [switch]$NoUpdate,
  [switch]$NoModel
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Say([string]$m, [string]$color = "Gray") {
  Write-Host $m -ForegroundColor $color
}
function Step([string]$m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }

$downloadDir = Join-Path $InstallDir "_download"
New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# ---------- 0. 增量包定位 ----------
if (-not $UpdateZip) {
  $cand = Get-ChildItem -Path (Split-Path -Parent $PSScriptRoot) -Directory -Filter "desktop-app-update-*-win32-x64" -ErrorAction SilentlyContinue |
          Sort-Object Name -Descending | Select-Object -First 1
  if ($cand) { $UpdateZip = $cand.FullName }
}

# ---------- 1. 取到压缩包 ----------
$archive = $null
if ($Package) {
  if (-not (Test-Path -LiteralPath $Package -PathType Leaf)) { throw "找不到压缩包：$Package" }
  $archive = (Resolve-Path -LiteralPath $Package).Path
  Say "使用本地压缩包：$archive"
} elseif ($Url) {
  Step "下载便携包（支持续传，可重复运行）"
  $name = [System.IO.Path]::GetFileName(([Uri]$Url).AbsolutePath)
  if (-not $name -or $name -notmatch '\.(zip|7z)$') { $name = "T8star-Aix-IndexTTS-portable.zip" }
  $target = Join-Path $downloadDir $name
  if (Test-Path -LiteralPath $target) {
    Say "已存在同名文件，尝试续传：$target"
    Start-BitsTransfer -Source $Url -Destination $target -TransferType Download -DisplayName "T8star-Aix portable"
  } else {
    Start-BitsTransfer -Source $Url -Destination $target -TransferType Download -DisplayName "T8star-Aix portable"
  }
  $archive = $target
} else {
  # 目录里已有压缩包就直接用
  $found = Get-ChildItem -Path $downloadDir -File -ErrorAction SilentlyContinue |
           Where-Object { $_.Extension -in @(".zip", ".7z") } |
           Sort-Object Length -Descending | Select-Object -First 1
  if ($found) { $archive = $found.FullName; Say "使用 _download 里已有的包：$archive" }
}
if (-not $archive) {
  throw "没有可用压缩包。请用 -Url <直链> 或 -Package <本地zip路径> 再运行一次。"
}
$sizeGB = [math]::Round((Get-Item -LiteralPath $archive).Length / 1GB, 2)
Say ("压缩包大小：{0} GB" -f $sizeGB) "Green"

# ---------- 2. 解压 ----------
$stamp = Join-Path $InstallDir ".extracted"
if (Test-Path -LiteralPath $stamp) {
  Say "检测到已解压标记，跳过解压（要重解请删除 $stamp）"
} else {
  Step "解压到 $InstallDir（几个 GB，需要几分钟）"
  $tmp = Join-Path $InstallDir "_extract_tmp"
  if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
  New-Item -ItemType Directory -Force -Path $tmp | Out-Null
  if ($archive -match '\.7z$') {
    $sevenZip = (Get-Command 7z -ErrorAction SilentlyContinue).Source
    if (-not $sevenZip) { throw "这是 7z 包，但系统里没有 7z 命令，请装 7-Zip 或换 zip 包" }
    & $sevenZip x $archive "-o$tmp" -y | Out-Null
  } else {
    Expand-Archive -LiteralPath $archive -DestinationPath $tmp -Force
  }
  # 把解压结果摊平到安装目录（自动跳过单层顶层目录）
  function Move-Children([string]$from, [string]$to) {
    Get-ChildItem -LiteralPath $from -Force | ForEach-Object {
      Move-Item -LiteralPath $_.FullName -Destination (Join-Path $to $_.Name) -Force
    }
  }
  if (Test-Path -LiteralPath (Join-Path $tmp "resources")) {
    Move-Children $tmp $InstallDir
  } else {
    $sub = Get-ChildItem -LiteralPath $tmp -Directory -Force |
           Where-Object { Test-Path (Join-Path $_.FullName "resources") } | Select-Object -First 1
    if ($sub) {
      Say "压缩包有一层顶层目录，正在跳过：$($sub.Name)"
      Move-Children $sub.FullName $InstallDir
    } else {
      Move-Children $tmp $InstallDir
    }
  }
  Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
  Set-Content -LiteralPath $stamp -Value (Get-Date -Format o) -Encoding UTF8
  Say "解压完成" "Green"
}

# ---------- 3. 叠加 0.26.4 增量 ----------
if (-not $NoUpdate -and $UpdateZip -and (Test-Path -LiteralPath (Join-Path $UpdateZip "resources"))) {
  Step "覆盖 0.26.4 应用层增量"
  $srcRes = Join-Path $UpdateZip "resources"
  $dstRes = Join-Path $InstallDir "resources"
  New-Item -ItemType Directory -Force -Path $dstRes | Out-Null
  Copy-Item -Path (Join-Path $srcRes "*") -Destination $dstRes -Recurse -Force
  Get-ChildItem -LiteralPath $UpdateZip -File | Where-Object { $_.Extension -eq ".cmd" -or $_.Extension -eq ".txt" } |
    ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $InstallDir -Force }
  Say "增量覆盖完成（app.asar / indextts / CMD 脚本）" "Green"
}

# ---------- 4. 校验 ----------
Step "校验运行时"
$res = Join-Path $InstallDir "resources"
$report = [ordered]@{}
$report["安装目录"] = $InstallDir
$report["Electron 可执行文件"] = [bool](Get-ChildItem -LiteralPath $InstallDir -Filter "*.exe" -File -ErrorAction SilentlyContinue)
$py = Get-ChildItem -Path (Join-Path $res "cpython-*") -Directory -ErrorAction SilentlyContinue |
      Where-Object { Test-Path (Join-Path $_.FullName "python.exe") } | Select-Object -First 1
$report["内置 Python"] = if ($py) { $py.FullName } else { $false }
$report["site-packages"] = Test-Path (Join-Path $res "site-packages")
$report["app.asar"] = Test-Path (Join-Path $res "app.asar")
$report["API 启动脚本"] = Test-Path (Join-Path $InstallDir "启动API服务.cmd")

foreach ($k in $report.Keys) {
  $v = $report[$k]
  $ok = if ($v -is [bool]) { $v } else { [bool]$v }
  $line = "  {0,-18} {1}" -f $k, $v
  if ($ok) { Write-Host $line -ForegroundColor Green } else { Write-Host $line -ForegroundColor Yellow }
}

$runtimeOk = ($report["内置 Python"] -ne $false) -and $report["site-packages"]
Write-Host ""
if ($runtimeOk) {
  Say "运行时齐全，可以启动 API 服务了：" "Green"
  Say "  双击 $InstallDir\启动API服务.cmd" "White"
  Say "  或先运行桌面 EXE，在启动器里选模型目录并设好端口/音色，再启动服务" "White"
  if (-not $NoModel) {
    Say "模型（约 10 GB）不在便携包里：请在启动器里点「Hugging Face 自动下载／修复完整模型」" "Yellow"
  }
} else {
  Say "运行时仍然不齐全 —— 说明你给的压缩包不是完整便携包（可能又是应用层增量包）。" "Red"
  Say "完整便携包解压后必须能看到 resources\cpython-3.10.20-windows-x86_64-none\python.exe" "Yellow"
  exit 2
}
