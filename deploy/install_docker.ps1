<#
  企业级 P0 · Docker 运行环境安装（Windows）

  用法（**需要管理员**，脚本会自己弹 UAC 提权）：
      powershell -ExecutionPolicy Bypass -File deploy\install_docker.ps1

  可选参数：
      -NoDockerDesktop   只启用 WSL2 / 虚拟机平台，不装 Docker Desktop
      -SkipWsl           跳过 WSL2 与虚拟机平台的功能启用（只装 Docker Desktop）
      -Restart           装完后自动重启（**默认不重启**：重启会中断正在跑的服务）

  纪律：
    · 本脚本**不重启**机器（除非显式 -Restart）；启用可选功能后需要人工重启才生效；
    · 日志写到 $PSScriptRoot\..\..\_shots\install_docker.log（不进版本库）；
    · 幂等：已启用的功能会跳过；Docker Desktop 已装则跳过安装。
#>
param(
  [switch]$NoDockerDesktop,
  [switch]$SkipWsl,
  [switch]$Restart
)

$ErrorActionPreference = 'Continue'
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$logDir = Join-Path $repoRoot '_shots'
$logFile = Join-Path $logDir 'install_docker.log'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

function Write-Log {
  param([string]$Message, [string]$Level = 'INFO')
  $line = "[{0}] [{1}] {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
  Write-Host $line
  Add-Content -Path $logFile -Value $line -Encoding UTF8
}

function Test-Admin {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  return (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ---------------------------------------------------------------- 提权自启
if (-not (Test-Admin)) {
  Write-Log '当前不是管理员：正在请求提权（请在弹出的 UAC 窗口点"是"）' 'WARN'
  $args = @('-ExecutionPolicy', 'Bypass', '-File', $MyInvocation.MyCommand.Path)
  foreach ($flag in @('NoDockerDesktop', 'SkipWsl', 'Restart')) {
    if ($args -contains $flag) { }
  }
  # 透传开关
  $extra = @()
  if ($NoDockerDesktop) { $extra += '-NoDockerDesktop' }
  if ($SkipWsl) { $extra += '-SkipWsl' }
  if ($Restart) { $extra += '-Restart' }
  $argLine = '-ExecutionPolicy Bypass -File "{0}" {1}' -f $MyInvocation.MyCommand.Path, ($extra -join ' ')
  try {
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $argLine -WindowStyle Normal
    Write-Log '已拉起提权窗口，等待安装完成…' 'INFO'
  } catch {
    Write-Log ("提权失败：{0}" -f $_.Exception.Message) 'FAIL'
  }
  exit 0
}

Write-Log '===== 开始安装 Docker 运行环境（管理员）=====' 'INFO'

# ---------------------------------------------------------------- 环境探测
Write-Log '--- 环境探测 ---' 'INFO'
$os = Get-CimInstance Win32_OperatingSystem
Write-Log ("系统：{0}  版本：{1}" -f $os.Caption, $os.Version)

$hypervisorPresent = $false
try {
  $cs = Get-CimInstance Win32_ComputerSystem
  $hypervisorPresent = [bool]$cs.HypervisorPresent
} catch { }
if ($hypervisorPresent) {
  Write-Log '检测到已有 Hypervisor：本机运行在虚拟机中。' 'WARN'
  Write-Log '  Linux 容器需要**嵌套虚拟化**（宿主要把 VT-x/AMD-V 透传给本机）。' 'WARN'
  Write-Log '  若宿主未透传，WSL2 与 Docker Desktop 会报 0x80370102（虚拟化未启用）。' 'WARN'
} else {
  Write-Log '未检测到 Hypervisor：物理机，可直接用 WSL2 后端。' 'INFO'
}

$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
Write-Log ("CPU：{0}" -f $cpu.Name)

# ---------------------------------------------------------------- 启用可选功能
function Enable-Feature {
  param([string]$Name, [string]$Label)
  Write-Log ("启用可选功能：{0}（{1}）…" -f $Label, $Name) 'INFO'
  $out = & dism.exe /online /enable-feature /featurename:$Name /all /norestart 2>&1
  $code = $LASTEXITCODE
  $tail = (($out | Out-String).Trim() -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 1)
  # 0 = 成功；3010 = 成功但需要重启（启用虚拟机平台后的正常结果）
  if ($code -eq 0) {
    Write-Log ("  已启用：{0}" -f $Label) 'OK'
  } elseif ($code -eq 3010) {
    Write-Log ("  已启用：{0}（3010 = 需要重启后生效）" -f $Label) 'OK'
  } else {
    Write-Log ("  启用失败（exit {0}）：{1}" -f $code, $tail) 'WARN'
  }
}

if (-not $SkipWsl) {
  Write-Log '--- 启用 WSL2 与虚拟机平台 ---' 'INFO'
  Enable-Feature 'Microsoft-Windows-Subsystem-Linux' '适用于 Linux 的 Windows 子系统'
  Enable-Feature 'VirtualMachinePlatform' '虚拟机平台'

  Write-Log '检查 wsl 命令…' 'INFO'
  $wslStatus = & wsl.exe --status 2>&1
  Write-Log ("wsl --status：{0}" -f (($wslStatus | Out-String).Trim() -replace "`r?`n", ' | '))
}

# ---------------------------------------------------------------- Docker Desktop
$dockerExe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
$dockerInstalled = Test-Path $dockerExe

if ($NoDockerDesktop) {
  Write-Log '按要求跳过 Docker Desktop 安装。' 'INFO'
} elseif ($dockerInstalled) {
  Write-Log 'Docker Desktop 已安装，跳过。' 'OK'
} else {
  Write-Log '--- 安装 Docker Desktop（winget）---' 'INFO'
  $winget = Get-Command winget -ErrorAction SilentlyContinue
  if ($null -eq $winget) {
    Write-Log '找不到 winget：请手动下载 https://www.docker.com/products/docker-desktop/' 'FAIL'
  } else {
    $out = & winget install -e --id Docker.DockerDesktop `
      --accept-package-agreements --accept-source-agreements --silent 2>&1
    $code = $LASTEXITCODE
    foreach ($line in ($out | Out-String).Trim() -split "`r?`n") {
      if ($line.Trim()) { Write-Log ("  winget：{0}" -f $line.Trim()) }
    }
    if ($code -eq 0 -or (Test-Path $dockerExe)) {
      Write-Log 'Docker Desktop 安装完成。' 'OK'
    } else {
      Write-Log ("winget 安装未成功（exit {0}），请手动安装。" -f $code) 'FAIL'
    }
  }
}

# ---------------------------------------------------------------- 收尾
Write-Log '===== 安装流程结束 =====' 'INFO'
Write-Log '下一步：' 'INFO'
Write-Log '  1) **重启一次**（启用虚拟机平台后必须重启才生效）' 'INFO'
Write-Log '  2) 重启后执行：powershell -ExecutionPolicy Bypass -File deploy\setup_windows.ps1 -Finish' 'INFO'
Write-Log '     （该命令会验证 docker、校验 compose、构建镜像、起服务、跑 4 个冒烟脚本）' 'INFO'
Write-Log '  3) 不想重启也能先用的方案：deploy\windows\start-native.ps1（原生部署，不依赖 Docker）' 'INFO'

if ($Restart) {
  Write-Log '按要求重启机器…' 'WARN'
  Restart-Computer -Force
}
