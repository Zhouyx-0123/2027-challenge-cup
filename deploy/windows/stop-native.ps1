<#
    物流企业碳感知智能决策系统 · Windows 原生部署停止脚本

    优先读 logs\native.pids.json 里记录的 PID 逐个停；没有 PID 文件时按端口找占用者，
    **默认只提示不杀**，必须显式 -Force 才动手。

    用法：
        powershell -ExecutionPolicy Bypass -File deploy\windows\stop-native.ps1
        powershell -ExecutionPolicy Bypass -File deploy\windows\stop-native.ps1 -Force

    纪律：
        · 本脚本**不需要管理员权限**（停自己启动的进程足够）。
        · -Force 会杀掉占用 8501/5000 的**任何**进程（包括别的程序），请谨慎使用。
#>
param(
    [switch]$Force,
    [int[]]$Ports = @(8501, 5000)
)

$ErrorActionPreference = 'Continue'

$script:RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$script:LogDir   = Join-Path $script:RepoRoot 'logs'
$script:PidFile  = Join-Path $script:LogDir 'native.pids.json'

function Write-Ok   { param([string]$M) Write-Host ('[OK]   ' + $M) -ForegroundColor Green }
function Write-Info { param([string]$M) Write-Host ('[INFO] ' + $M) -ForegroundColor Cyan }
function Write-Warn { param([string]$M) Write-Host ('[WARN] ' + $M) -ForegroundColor Yellow }
function Write-Fail { param([string]$M) Write-Host ('[FAIL] ' + $M) -ForegroundColor Red }

function Test-ProcessAlive {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return $false }
    return $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

# 监听进程查询：CIM 为主，netstat 兜底。
# 调用方一律用 @( ) 包一层：PowerShell 会把「单个元素」的数组自动摊平，
# 直接取 .Count 会拿到 $null，导致「端口被占用」被误判成「端口空闲」。
function Get-PortListener {
    param([int]$Port)
    $found = @()
    try {
        foreach ($row in @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) {
            $found += [pscustomobject]@{ LocalPort = $Port; OwningProcess = [int]$row.OwningProcess }
        }
    } catch { }
    if ($found.Count -gt 0) { return $found }

    $pattern = '^\s*TCP\s+\S+:' + $Port + '\s+\S+\s+LISTENING\s+(\d+)\s*$'
    try {
        foreach ($line in (& netstat.exe -ano 2>$null)) {
            if ($line -match $pattern) {
                $found += [pscustomobject]@{ LocalPort = $Port; OwningProcess = [int]$Matches[1] }
            }
        }
    } catch { }
    return $found
}

function Stop-ProcessTree {
    param([int]$ProcessId, [string]$Label)
    if (-not (Test-ProcessAlive -ProcessId $ProcessId)) {
        Write-Info ($Label + '（PID ' + $ProcessId + '）本来就没在跑。')
        return $true
    }
    try {
        Stop-Process -Id $ProcessId -Force -ErrorAction Stop
    } catch {
        Write-Warn ('Stop-Process 失败，改用 taskkill /T /F：' + $_.Exception.Message)
    }
    $deadline = (Get-Date).AddSeconds(6)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-ProcessAlive -ProcessId $ProcessId)) {
            Write-Ok ('已停止 ' + $Label + '（PID ' + $ProcessId + '）')
            return $true
        }
        Start-Sleep -Milliseconds 400
    }
    # 兜底：连带子进程一起杀
    $null = & taskkill.exe /PID $ProcessId /T /F 2>&1
    Start-Sleep -Milliseconds 800
    if (Test-ProcessAlive -ProcessId $ProcessId) {
        Write-Fail ('停不掉 ' + $Label + '（PID ' + $ProcessId + '），请手动结束该进程。')
        return $false
    }
    Write-Ok ('已停止 ' + $Label + '（PID ' + $ProcessId + '，taskkill 兜底）')
    return $true
}

Write-Host ''
Write-Host '============================================================' -ForegroundColor DarkCyan
Write-Host '  物流企业碳感知智能决策系统 · 原生部署停止' -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor DarkCyan
Write-Info ('仓库根目录：' + $script:RepoRoot)

$stopped = 0

# ---------------------------------------------------------------- 1) 按 PID 文件停
$pidData = $null
if (Test-Path $script:PidFile) {
    try {
        $raw = [System.IO.File]::ReadAllText($script:PidFile, [System.Text.Encoding]::UTF8)
        if (-not [string]::IsNullOrWhiteSpace($raw)) { $pidData = $raw | ConvertFrom-Json }
    } catch {
        Write-Warn ('PID 文件解析失败：' + $_.Exception.Message)
    }
}

if ($null -ne $pidData) {
    if ($null -ne $pidData.app) {
        if (Stop-ProcessTree -ProcessId ([int]$pidData.app.pid) -Label ('决策台(端口 ' + $pidData.app.port + ')')) { $stopped++ }
    }
    if ($null -ne $pidData.sensor) {
        if (Stop-ProcessTree -ProcessId ([int]$pidData.sensor.pid) -Label ('传感器网关(端口 ' + $pidData.sensor.port + ')')) { $stopped++ }
    }
    Remove-Item -Path $script:PidFile -Force -ErrorAction SilentlyContinue
    Write-Ok 'PID 文件已清理。'
} else {
    Write-Warn ('没有找到 PID 文件（' + $script:PidFile + '），改为按端口检查。')
}

# ---------------------------------------------------------------- 2) 按端口兜底检查
$remaining = @()
foreach ($p in $Ports) {
    $listeners = @(Get-PortListener -Port $p)
    if ($listeners.Count -eq 0) { continue }
    foreach ($l in $listeners) {
        $ownerPid = [int]$l.OwningProcess
        $procName = (Get-Process -Id $ownerPid -ErrorAction SilentlyContinue).ProcessName
        if ($null -eq $procName) { $procName = '未知进程' }
        $remaining += [pscustomobject]@{ Port = $p; Pid = $ownerPid; Name = $procName }
    }
}

if ($remaining.Count -eq 0) {
    if ($stopped -gt 0) {
        Write-Ok '全部服务已停止，端口 8501 / 5000 已释放。'
    } else {
        Write-Info '没有发现运行中的原生服务，无需停止。'
    }
    exit 0
}

Write-Host ''
Write-Warn '以下端口仍被占用（**不是本脚本记录的进程**，多半是别的程序或父会话起的服务）：'
foreach ($r in $remaining) {
    Write-Host ('       端口 ' + $r.Port + '  PID ' + $r.Pid + '  ' + $r.Name) -ForegroundColor Yellow
}

if (-not $Force) {
    Write-Info '默认**不杀**这些进程。如果确认要停，请加 -Force：'
    Write-Info '    powershell -ExecutionPolicy Bypass -File deploy\windows\stop-native.ps1 -Force'
    Write-Warn '注意：-Force 会结束占用端口的任何进程（包括非本项目程序）。'
    exit 0
}

foreach ($r in $remaining) {
    if (Stop-ProcessTree -ProcessId $r.Pid -Label ('端口 ' + $r.Port + ' 上的进程')) { $stopped++ }
}

Write-Host ''
Write-Ok ('共停止 ' + $stopped + ' 个进程。')
exit 0
