<#
    物流企业碳感知智能决策系统 · Windows 原生部署启动脚本（不依赖 Docker）

    一条命令把「决策台(Streamlit, 8501)」与「传感器网关(Flask, 5000)」拉起来，
    并做健康检查、打印局域网访问地址、记录 PID 便于 stop-native.ps1 停止。

    用法：
        powershell -ExecutionPolicy Bypass -File deploy\windows\start-native.ps1
        powershell -ExecutionPolicy Bypass -File deploy\windows\start-native.ps1 -NoSensor
        powershell -ExecutionPolicy Bypass -File deploy\windows\start-native.ps1 -SensorPort 5051
        powershell -ExecutionPolicy Bypass -File deploy\windows\start-native.ps1 -Foreground

    参数：
        -AppPort      决策台端口（默认 8501）
        -SensorPort   传感器网关端口（默认 5000）
        -NoSensor     不启动传感器网关（只起决策台）
        -Foreground   前台运行决策台（Ctrl+C 停止），用于排障

    纪律：
        · 本脚本**不需要管理员权限**（只有防火墙/开机自启才需要）。
        · 端口被占用时**不会**杀别人的进程：只提示，由使用者决定。
        · 启动后会核对「端口到底是谁在监听」，防止把别人的服务误判成自己启动成功。
        · 日志写 <仓库根>\logs\（不进版本库，父会话需把 logs/ 加进 .gitignore）。
#>
param(
    [int]$AppPort = 8501,
    [int]$SensorPort = 5000,
    [switch]$NoSensor,
    [switch]$Foreground
)

$ErrorActionPreference = 'Continue'

# ---------------------------------------------------------------- 路径自定位
$script:RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$script:LogDir   = Join-Path $script:RepoRoot 'logs'
$script:PidFile  = Join-Path $script:LogDir 'native.pids.json'

# ---------------------------------------------------------------- 输出helper
function Write-Ok   { param([string]$M) Write-Host ('[OK]   ' + $M) -ForegroundColor Green }
function Write-Info { param([string]$M) Write-Host ('[INFO] ' + $M) -ForegroundColor Cyan }
function Write-Warn { param([string]$M) Write-Host ('[WARN] ' + $M) -ForegroundColor Yellow }
function Write-Fail { param([string]$M) Write-Host ('[FAIL] ' + $M) -ForegroundColor Red }

# ---------------------------------------------------------------- 小工具函数
function Test-ProcessAlive {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return $false }
    return $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Get-PortOwnerName {
    param([int]$ProcessId)
    $p = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $p) { return '未知进程' }
    return $p.ProcessName
}

# 监听进程查询：先用 CIM，查不到再用 netstat 兜底（两者都不依赖管理员权限）
# 注意：调用方必须用 @( ) 包一层——PowerShell 会把「单个元素」的数组自动摊平，
#       直接取 .Count 会拿到 $null，导致「端口被占用」被误判成「端口空闲」。
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

# 返回 @{ State = 'none' | 'ours' | 'foreign'; Pid = <占用端口的 PID> }
# 注意：只有在「记录的端口 == 要查的端口」时，记录的 PID 才算数——
#       否则换个端口启动时会把别的端口上的服务误当成「已在运行」。
function Get-PortState {
    param([int]$Port, [int]$RecordedPid, [int]$RecordedPort = 0)
    $recorded = ($RecordedPort -eq $Port)
    $listeners = @(Get-PortListener -Port $Port)
    if ($listeners.Count -gt 0) {
        foreach ($l in $listeners) {
            if ($recorded -and $RecordedPid -gt 0 -and [int]$l.OwningProcess -eq $RecordedPid) {
                return @{ State = 'ours'; Pid = [int]$l.OwningProcess }
            }
        }
        return @{ State = 'foreign'; Pid = [int]$listeners[0].OwningProcess }
    }
    if ($recorded -and $RecordedPid -gt 0 -and (Test-ProcessAlive -ProcessId $RecordedPid)) {
        # 进程还在但还没开始监听（例如刚启动）：也算「已经在跑」
        return @{ State = 'ours'; Pid = $RecordedPid }
    }
    return @{ State = 'none'; Pid = 0 }
}

function Wait-PortOwnedBy {
    param([int]$Port, [int]$ProcessId, [int]$TimeoutSec = 45)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        foreach ($l in @(Get-PortListener -Port $Port)) {
            if ([int]$l.OwningProcess -eq $ProcessId) { return $true }
        }
        if (-not (Test-ProcessAlive -ProcessId $ProcessId)) { return $false }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Get-LanIPv4 {
    try {
        $addr = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object { $_.IPAddress -notlike '127.*' -and $_.PrefixOrigin -ne 'WellKnown' } |
            Select-Object -First 1
        if ($null -eq $addr) { return '127.0.0.1' }
        return $addr.IPAddress
    } catch {
        return '127.0.0.1'
    }
}

function Wait-HttpHealthy {
    param([string]$Url, [int]$TimeoutSec = 60)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 4
            if ($resp.StatusCode -eq 200) { return $true }
        } catch {
            # 连接被拒绝 / 超时都视为「还没起来」，继续等
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Read-PidFile {
    if (-not (Test-Path $script:PidFile)) { return $null }
    try {
        $raw = [System.IO.File]::ReadAllText($script:PidFile, [System.Text.Encoding]::UTF8)
        if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
        return ($raw | ConvertFrom-Json)
    } catch {
        Write-Warn ("PID 文件解析失败（将忽略）：" + $_.Exception.Message)
        return $null
    }
}

function Write-PidFile {
    param([hashtable]$Payload)
    $json = $Payload | ConvertTo-Json -Depth 6
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($script:PidFile, $json, $utf8NoBom)
}

# ---------------------------------------------------------------- 服务启动器
# 为什么要这一层启动器（logs\app_launcher.py / logs\sensor_launcher.py）：
#   1) 直接 Start-Process python -m streamlit 时，服务进程**及其子进程**会继承调用方的
#      stdout 管道句柄 → `start-native.ps1 | Tee-Object` 永远等不到 EOF（控制台像卡死）。
#      启动器里 os.dup2 把 fd 1/2 换成日志文件，等于主动放掉管道句柄。
#   2) .NET 的 -RedirectStandardOutput 会以「拒绝写入」方式独占日志文件，
#      上一次的服务还在跑时，下一次启动就打不开日志。启动器用 Python 打开日志
#      （共享模式，可追加），就不再有这个问题。
# 启动器放在 logs/ 下，是运行时产物，不进版本库。
function Write-Launcher {
    param([string]$Path, [string[]]$Lines)
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, (($Lines -join "`n") + "`n"), $utf8NoBom)
    return $Path
}

function Get-AppLauncherPath {
    param([int]$Port)
    $lines = @(
        '# -*- coding: utf-8 -*-',
        '"""由 start-native.ps1 生成：把 stdout/stderr 接到日志文件后启动 Streamlit 决策台。"""',
        'import os, runpy, sys',
        'out = open(os.environ["CARBONTIDE_APP_OUT"], "a", encoding="utf-8", errors="replace", buffering=1)',
        'err = open(os.environ["CARBONTIDE_APP_ERR"], "a", encoding="utf-8", errors="replace", buffering=1)',
        'os.dup2(out.fileno(), 1)',
        'os.dup2(err.fileno(), 2)',
        'sys.stdout = out',
        'sys.stderr = err',
        'port = os.environ.get("CARBONTIDE_APP_PORT", "8501")',
        'sys.argv = ["streamlit", "run", "app/app.py", "--server.port", port,',
        '            "--server.address", "0.0.0.0", "--server.headless", "true"]',
        'runpy.run_module("streamlit", run_name="__main__")'
    )
    return Write-Launcher -Path (Join-Path $script:LogDir 'app_launcher.py') -Lines $lines
}

function Get-SensorLauncherPath {
    param([int]$Port)
    $lines = @(
        '# -*- coding: utf-8 -*-',
        '"""由 start-native.ps1 生成：把 stdout/stderr 接到日志文件后启动传感器网关。"""',
        'import os, runpy, sys',
        'out = open(os.environ["CARBONTIDE_SENSOR_OUT"], "a", encoding="utf-8", errors="replace", buffering=1)',
        'err = open(os.environ["CARBONTIDE_SENSOR_ERR"], "a", encoding="utf-8", errors="replace", buffering=1)',
        'os.dup2(out.fileno(), 1)',
        'os.dup2(err.fileno(), 2)',
        'sys.stdout = out',
        'sys.stderr = err',
        'root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))',
        'sys.path.insert(0, root)',
        'port = int(os.environ.get("CARBONTIDE_SENSOR_PORT", "5000"))',
        'if port == 5000:',
        '    # 与 `python server.py` 完全一致：走 server.py 的 __main__ 分支',
        '    runpy.run_path(os.path.join(root, "server.py"), run_name="__main__")',
        'else:',
        '    # server.py 把端口写死成 5000；换端口时用同一份 app，不改业务代码',
        '    import server',
        '    server.app.run(host="0.0.0.0", port=port, debug=False)'
    )
    return Write-Launcher -Path (Join-Path $script:LogDir 'sensor_launcher.py') -Lines $lines
}

# 启动一个后台服务进程，返回服务进程 PID
function Start-NativeService {
    param(
        [string]$LauncherPath,
        [string]$WorkingDirectory
    )
    $proc = Start-Process -FilePath $script:PythonExeFull -ArgumentList @($LauncherPath) `
        -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -PassThru
    return $proc.Id
}

# ---------------------------------------------------------------- 环境自检
Write-Host ''
Write-Host '============================================================' -ForegroundColor DarkCyan
Write-Host '  物流企业碳感知智能决策系统 · 原生部署启动' -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor DarkCyan
Write-Info ("仓库根目录：" + $script:RepoRoot)

if (-not (Test-Path $script:RepoRoot)) {
    Write-Fail '仓库根目录不存在，脚本位置可能不对。'
    exit 1
}
Set-Location $script:RepoRoot

if (-not (Test-Path $script:LogDir)) {
    New-Item -ItemType Directory -Path $script:LogDir -Force | Out-Null
    Write-Info ('已创建日志目录：' + $script:LogDir)
}

# --- ① 找 Python（要求 >= 3.10）---
$pythonExe = $null
$pythonExeFull = $null
$pythonPre = @()
$pyCandidates = @()
$cmdPython = Get-Command python -ErrorAction SilentlyContinue
if ($null -ne $cmdPython) { $pyCandidates += ,@('python', @(), $cmdPython.Source) }
$cmdPy = Get-Command py -ErrorAction SilentlyContinue
if ($null -ne $cmdPy) { $pyCandidates += ,@('py', @('-3'), $cmdPy.Source) }

foreach ($cand in $pyCandidates) {
    $exe = $cand[0]
    $pre = $cand[1]
    $exeFull = $cand[2]
    $verOut = & $exe @pre -c "import sys; print('%d.%d.%d' % sys.version_info[:3])" 2>$null
    if ($LASTEXITCODE -eq 0 -and $verOut) {
        $verText = ($verOut | Select-Object -First 1).ToString().Trim()
        $parts = $verText.Split('.')
        if ([int]$parts[0] -gt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 10)) {
            $pythonExe = $exe
            $pythonExeFull = $exeFull
            $pythonPre = $pre
            Write-Ok ("Python " + $verText + "（" + $exe + "）")
            break
        } else {
            Write-Warn ("Python " + $verText + " 版本过低（需要 >= 3.10），继续找下一个解释器…")
        }
    }
}

if ($null -eq $pythonExe) {
    Write-Fail '找不到可用的 Python（>= 3.10）。'
    Write-Info '请先安装 Python 3.10+ 并勾选 "Add python.exe to PATH"，然后重试。'
    exit 1
}
$script:PythonExeFull = $pythonExeFull

# --- ② 依赖齐备（flask_cors 缺失只提示，不崩）---
$depOut = & $pythonExe @pythonPre -c "import streamlit, pandas, pyarrow, flask; print('deps-ok')" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Fail '依赖缺失：streamlit / pandas / pyarrow / flask 至少有一个装不上。'
    Write-Info ('  原始错误：' + (($depOut | Out-String).Trim()))
    Write-Info '  修复：pip install -r requirements.txt（或先装离线包 wheels\ 里的 whl）'
    exit 1
}
Write-Ok '核心依赖齐备（streamlit / pandas / pyarrow / flask）'

$null = & $pythonExe @pythonPre -c "import flask_cors" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Warn '缺少 flask-cors：服务仍可启动，只是浏览器跨域访问时不发跨域头。'
    Write-Info '  如需补齐：pip install flask-cors（或 pip install -r requirements.txt）'
}

# ---------------------------------------------------------------- 读旧 PID
$existing = Read-PidFile
$existingAppPid = 0
$existingAppPort = 0
$existingSensorPid = 0
$existingSensorPort = 0
if ($null -ne $existing) {
    if ($null -ne $existing.app) {
        $existingAppPid  = [int]$existing.app.pid
        $existingAppPort = [int]$existing.app.port
    }
    if ($null -ne $existing.sensor) {
        $existingSensorPid  = [int]$existing.sensor.pid
        $existingSensorPort = [int]$existing.sensor.port
    }
}

# ---------------------------------------------------------------- 端口占用判断
$appState = Get-PortState -Port $AppPort -RecordedPid $existingAppPid -RecordedPort $existingAppPort
$sensorState = @{ State = 'none'; Pid = 0 }
if (-not $NoSensor) {
    $sensorState = Get-PortState -Port $SensorPort -RecordedPid $existingSensorPid -RecordedPort $existingSensorPort
}

if ($appState.State -eq 'foreign') {
    Write-Fail ("端口 " + $AppPort + " 已被占用（PID " + $appState.Pid + "：" + (Get-PortOwnerName -ProcessId $appState.Pid) + "），不是本脚本启动的进程。")
    Write-Info '  处理办法：① 用 stop-native.ps1 -Force 停掉占用者；② 或换端口重试，例如 -AppPort 8511。'
    Write-Info '  本脚本**不会**自动杀别人的进程。'
    exit 1
}
if ($appState.State -eq 'ours') {
    Write-Ok ("决策台已在运行（PID " + $appState.Pid + "，端口 " + $AppPort + "）")
}

if (-not $NoSensor -and $sensorState.State -eq 'foreign') {
    Write-Fail ("端口 " + $SensorPort + " 已被占用（PID " + $sensorState.Pid + "：" + (Get-PortOwnerName -ProcessId $sensorState.Pid) + "），不是本脚本启动的进程。")
    Write-Info '  处理办法：① 用 stop-native.ps1 -Force 停掉占用者；② 或换端口重试，例如 -SensorPort 5051。'
    Write-Info '  本脚本**不会**自动杀别人的进程。'
    exit 1
}
if (-not $NoSensor -and $sensorState.State -eq 'ours') {
    Write-Ok ("传感器网关已在运行（PID " + $sensorState.Pid + "，端口 " + $SensorPort + "）")
}

$lanIp = Get-LanIPv4

if ($appState.State -eq 'ours' -and (-not $NoSensor) -and $sensorState.State -eq 'ours') {
    Write-Ok '两个服务都已在运行，脚本不做任何改动。'
    Write-Host ''
    Write-Info ("决策台访问地址：    http://" + $lanIp + ":" + $AppPort)
    Write-Info ("传感器接入点(ESP32)：http://" + $lanIp + ":" + $SensorPort + "/api/upload_sensor")
    Write-Info ("本机健康检查：      http://127.0.0.1:" + $AppPort + "/_stcore/health")
    exit 0
}

# ---------------------------------------------------------------- 启动
$startedAppPid = 0
$startedSensorPid = 0
$appOwned = ($appState.State -eq 'ours')
$sensorOwned = ($sensorState.State -eq 'ours')

if ($appState.State -eq 'none' -and -not $Foreground) {
    $env:CARBONTIDE_APP_OUT = Join-Path $script:LogDir 'app.out.log'
    $env:CARBONTIDE_APP_ERR = Join-Path $script:LogDir 'app.err.log'
    $env:CARBONTIDE_APP_PORT = "$AppPort"
    $launcher = Get-AppLauncherPath -Port $AppPort
    $startedAppPid = Start-NativeService -LauncherPath $launcher -WorkingDirectory $script:RepoRoot
    Write-Ok ("决策台进程已拉起（PID " + $startedAppPid + "，端口 " + $AppPort + "）")
}

if ($NoSensor) {
    Write-Info '按要求跳过传感器网关（-NoSensor）。'
} elseif ($sensorState.State -eq 'none') {
    $env:CARBONTIDE_SENSOR_OUT = Join-Path $script:LogDir 'sensor.out.log'
    $env:CARBONTIDE_SENSOR_ERR = Join-Path $script:LogDir 'sensor.err.log'
    $env:CARBONTIDE_SENSOR_PORT = "$SensorPort"
    $launcher = Get-SensorLauncherPath -Port $SensorPort
    $startedSensorPid = Start-NativeService -LauncherPath $launcher -WorkingDirectory $script:RepoRoot
    Write-Ok ("传感器网关进程已拉起（PID " + $startedSensorPid + "，端口 " + $SensorPort + "）")
}

# ---------------------------------------------------------------- 前台模式
if ($Foreground) {
    Write-Host ''
    Write-Info '前台模式：决策台日志直接打屏，按 Ctrl+C 停止。'
    Write-Info ("决策台地址：http://" + $lanIp + ":" + $AppPort)
    if (-not $NoSensor) {
        Write-Info ("传感器地址：http://" + $lanIp + ":" + $SensorPort + "/api/upload_sensor")
    }
    try {
        & $pythonExe @pythonPre -m streamlit run app/app.py --server.port $AppPort --server.address 0.0.0.0 --server.headless true
    } finally {
        if ($startedSensorPid -gt 0) {
            Write-Info '前台模式结束，停止后台的传感器网关…'
            Stop-Process -Id $startedSensorPid -Force -ErrorAction SilentlyContinue
        }
    }
    exit 0
}

# ---------------------------------------------------------------- 核对端口归属
# 只有确认「端口是我的 PID 在监听」，健康检查才算数；否则可能是别人的服务在应答。
$failed = 0
if ($startedAppPid -gt 0) {
    if (Wait-PortOwnedBy -Port $AppPort -ProcessId $startedAppPid -TimeoutSec 45) {
        $appOwned = $true
        Write-Ok ("决策台已在端口 " + $AppPort + " 上监听（PID " + $startedAppPid + "）")
    } else {
        $now = Get-PortState -Port $AppPort -RecordedPid 0
        if ($now.State -eq 'foreign') {
            Write-Fail ("端口 " + $AppPort + " 被别的进程抢占了（PID " + $now.Pid + "），本进程已退出。请换端口或先释放该端口。")
        } else {
            Write-Fail ("决策台启动后没能监听端口 " + $AppPort + "，详见 logs\app.err.log")
        }
        Stop-Process -Id $startedAppPid -Force -ErrorAction SilentlyContinue
        $failed++
    }
}

if ($startedSensorPid -gt 0) {
    if (Wait-PortOwnedBy -Port $SensorPort -ProcessId $startedSensorPid -TimeoutSec 45) {
        $sensorOwned = $true
        Write-Ok ("传感器网关已在端口 " + $SensorPort + " 上监听（PID " + $startedSensorPid + "）")
    } else {
        $now = Get-PortState -Port $SensorPort -RecordedPid 0
        if ($now.State -eq 'foreign') {
            Write-Fail ("端口 " + $SensorPort + " 被别的进程抢占了（PID " + $now.Pid + "），本进程已退出。")
        } else {
            Write-Fail ("传感器网关启动后没能监听端口 " + $SensorPort + "，详见 logs\sensor.err.log")
        }
        Stop-Process -Id $startedSensorPid -Force -ErrorAction SilentlyContinue
        $failed++
    }
}

# ---------------------------------------------------------------- 健康检查
Write-Info '健康检查中（最长 60 秒）…'
$appHealthy = $false
if ($appOwned) {
    $appHealthy = Wait-HttpHealthy -Url ("http://127.0.0.1:" + $AppPort + "/_stcore/health") -TimeoutSec 60
    if ($appHealthy) { Write-Ok '决策台健康检查通过（/_stcore/health = 200）' }
    else { Write-Fail '决策台健康检查超时，详见 logs\app.err.log'; $failed++ }
} else {
    $failed++
}

$sensorHealthy = $false
if (-not $NoSensor) {
    if ($sensorOwned) {
        $sensorHealthy = Wait-HttpHealthy -Url ("http://127.0.0.1:" + $SensorPort + "/api/health") -TimeoutSec 60
        if ($sensorHealthy) { Write-Ok '传感器网关健康检查通过（/api/health = 200）' }
        else { Write-Fail '传感器网关健康检查超时，详见 logs\sensor.err.log'; $failed++ }
    } else {
        $failed++
    }
}

# ---------------------------------------------------------------- 记 PID
$payload = @{
    startedAt = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    repoRoot  = $script:RepoRoot
    app       = $null
    sensor    = $null
}
$appPidForFile = if ($startedAppPid -gt 0) { $startedAppPid } elseif ($appState.State -eq 'ours') { $appState.Pid } else { 0 }
$payload.app = @{
    pid    = $appPidForFile
    port   = $AppPort
    url    = ("http://127.0.0.1:" + $AppPort)
    health = ("http://127.0.0.1:" + $AppPort + "/_stcore/health")
}
if (-not $NoSensor) {
    $sensorPidForFile = if ($startedSensorPid -gt 0) { $startedSensorPid } elseif ($sensorState.State -eq 'ours') { $sensorState.Pid } else { 0 }
    $payload.sensor = @{
        pid    = $sensorPidForFile
        port   = $SensorPort
        url    = ("http://127.0.0.1:" + $SensorPort)
        health = ("http://127.0.0.1:" + $SensorPort + "/api/health")
        esp32  = ("http://" + $lanIp + ":" + $SensorPort + "/api/upload_sensor")
    }
}
if ($failed -eq 0) {
    Write-PidFile -Payload $payload
    Write-Info ('PID 已记录：' + $script:PidFile)
} else {
    # 部分失败时也要落 PID 文件：否则 stop-native.ps1 找不到这些残留进程。
    Write-PidFile -Payload $payload
    Write-Warn ('启动过程有异常（失败项 ' + $failed + '），PID 文件仍已更新，可随时用 stop-native.ps1 清理。')
}

# ---------------------------------------------------------------- 访问地址
Write-Host ''
Write-Host '------------------------------------------------------------' -ForegroundColor DarkCyan
Write-Host '  访问地址' -ForegroundColor Cyan
Write-Host '------------------------------------------------------------' -ForegroundColor DarkCyan
Write-Host ("  决策台(Streamlit)   ：http://" + $lanIp + ":" + $AppPort)
if (-not $NoSensor) {
    Write-Host ("  传感器接入点(ESP32) ：http://" + $lanIp + ":" + $SensorPort + "/api/upload_sensor")
}
Write-Host ("  本机健康检查        ：http://127.0.0.1:" + $AppPort + "/_stcore/health")
Write-Host ("  日志目录            ：" + $script:LogDir)
Write-Host '------------------------------------------------------------' -ForegroundColor DarkCyan
Write-Warn 'ESP32 与本机必须在同一局域网网段，且防火墙已放行（deploy\windows\firewall.ps1）。'
Write-Info '停止服务：powershell -ExecutionPolicy Bypass -File deploy\windows\stop-native.ps1'

exit ($(if ($failed -eq 0) { 0 } else { 1 }))
