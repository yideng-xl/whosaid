$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Windows x64 自包含运行时：可重定位 Python + CPU 推理依赖 + core + ffmpeg。
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DesktopDir = Split-Path -Parent $ScriptDir
$RepoRoot = Split-Path -Parent $DesktopDir
$StagingRoot = Join-Path $DesktopDir "src-tauri"
$CoreSource = Join-Path $RepoRoot "core/transcribe_core"
$Requirements = Join-Path $ScriptDir "requirements-windows.lock"

$PythonBuildTag = "20260623"
$PythonAsset = "cpython-3.13.14+$PythonBuildTag-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
$PythonUrl = "https://github.com/astral-sh/python-build-standalone/releases/download/$PythonBuildTag/$PythonAsset"

# 固定版本，避免浮动下载地址在构建当天悄悄更换二进制。
$FfmpegAsset = "ffmpeg-8.0.1-essentials_build.zip"
$FfmpegUrl = "https://www.gyan.dev/ffmpeg/builds/packages/$FfmpegAsset"

if (-not (Test-Path $Requirements -PathType Leaf)) {
  throw "缺少 Windows 依赖锁文件：$Requirements"
}
if (-not (Test-Path $CoreSource -PathType Container)) {
  throw "找不到内核源码：$CoreSource"
}

$TempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("whosaid-runtime-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $TempDir | Out-Null

try {
  Write-Host "== 1/6 清空旧产物 =="
  foreach ($Name in @("python", "core", "ffmpeg")) {
    $Target = Join-Path $StagingRoot $Name
    if (Test-Path $Target) {
      Remove-Item -Recurse -Force $Target
    }
  }

  Write-Host "== 2/6 下载 Windows Python：$PythonAsset =="
  $PythonArchive = Join-Path $TempDir $PythonAsset
  curl.exe -fL --retry 3 -o $PythonArchive $PythonUrl
  if ($LASTEXITCODE -ne 0) {
    throw "Python 下载失败"
  }
  tar.exe -xzf $PythonArchive -C $StagingRoot
  if ($LASTEXITCODE -ne 0) {
    throw "Python 解压失败"
  }

  $Python = Join-Path $StagingRoot "python/python.exe"
  if (-not (Test-Path $Python -PathType Leaf)) {
    throw "解压后未找到 $Python"
  }

  Write-Host "== 3/6 安装 Windows CPU 依赖 =="
  & $Python -m pip install --no-cache-dir --upgrade pip
  & $Python -m pip install --no-cache-dir -r $Requirements

  Write-Host "== 4/6 清理缓存和包内测试目录 =="
  $SitePackages = & $Python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"
  Get-ChildItem $SitePackages -Directory -Recurse -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force
  Get-ChildItem $SitePackages -Directory -Depth 1 -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in @("test", "tests") } |
    Remove-Item -Recurse -Force
  Get-ChildItem (Join-Path $StagingRoot "python") -File -Recurse -Filter "*.pyc" -ErrorAction SilentlyContinue |
    Remove-Item -Force

  Write-Host "== 5/6 复制 transcribe_core =="
  $CoreTarget = Join-Path $StagingRoot "core/transcribe_core"
  New-Item -ItemType Directory -Path (Split-Path -Parent $CoreTarget) | Out-Null
  Copy-Item -Recurse -Force $CoreSource $CoreTarget

  Write-Host "== 6/6 下载并提取 ffmpeg/ffprobe =="
  $FfmpegArchive = Join-Path $TempDir $FfmpegAsset
  curl.exe -fL --retry 3 -o $FfmpegArchive $FfmpegUrl
  if ($LASTEXITCODE -ne 0) {
    throw "ffmpeg 下载失败"
  }
  $FfmpegExtract = Join-Path $TempDir "ffmpeg-extract"
  Expand-Archive -Path $FfmpegArchive -DestinationPath $FfmpegExtract
  $FfmpegSource = Get-ChildItem $FfmpegExtract -File -Recurse -Filter "ffmpeg.exe" |
    Select-Object -First 1
  $FfprobeSource = Get-ChildItem $FfmpegExtract -File -Recurse -Filter "ffprobe.exe" |
    Select-Object -First 1
  if ($null -eq $FfmpegSource -or $null -eq $FfprobeSource) {
    throw "ffmpeg 压缩包中未找到 ffmpeg.exe/ffprobe.exe"
  }
  $FfmpegTarget = Join-Path $StagingRoot "ffmpeg"
  New-Item -ItemType Directory -Path $FfmpegTarget | Out-Null
  Copy-Item $FfmpegSource.FullName (Join-Path $FfmpegTarget "ffmpeg.exe")
  Copy-Item $FfprobeSource.FullName (Join-Path $FfmpegTarget "ffprobe.exe")

  $env:PYTHONPATH = Join-Path $StagingRoot "core"
  & $Python -c "import fastapi, faster_whisper, pyannote.audio; print('bundled imports: ok')"
  & (Join-Path $FfmpegTarget "ffmpeg.exe") -version

  Write-Host "完成："
  Write-Host "  $Python"
  Write-Host "  $CoreTarget"
  Write-Host "  $FfmpegTarget"
}
finally {
  if (Test-Path $TempDir) {
    Remove-Item -Recurse -Force $TempDir
  }
}
