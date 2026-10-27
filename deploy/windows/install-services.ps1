<#
    物流企业碳感知智能决策系统 · Windows 开机自启（任务计划程序）

    把「启动原生部署」注册成计划任务：登录时（或开机时）自动拉起决策台 + 传感器网关。

    用法：
        powershell -ExecutionPolicy Bypass -File deploy\windows\install-services.ps1
        powershell -ExecutionPolicy Bypass -File deploy\windows\install-services.ps1 -AtStartup
        powershell -ExecutionPolicy Bypass -File deploy\windows\install-services.ps1 -Uninstall

    参数：
        -Uninstall   反注册（删除计划任务）
        -AtStartup   用「系统启动时 + SYSTEM 账户」而不是「用户登录时」
        -AppPort     决策台端口（默认 8501）
        -SensorPort  传感器网关端口（默认 5000）

    纪律：
        · **需要管理员权限**（脚本自己会 Start-Process -Verb RunAs 弹 UAC 提权重跑）。
        · 不用 NSSM 等第三方包装器：多一个外部 exe 就多一份下载、杀软误报与维护成本；
          计划任务是 Windows 自带能力，卸载只需一条命令，出问题也能用 schtasks 查。
#>
param(
    [switch]$Uninstall,
    [switch]$AtStartup,
    [int]$AppPort = 8501,
    [int]$SensorPort = 5000
)

$ErrorActionPreference = 'Continue'

$script:TaskName = 'CarbonTide Native'
$script:WindowsDir = Split-Path -Parent $PSScriptRoot
$script:StartScript = Join-Path $script:WindowsDir 'start-native.ps1'
$script:RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

function Write-Ok   { param([string]$M) Write-Host ('[OK]   ' + $M) -ForegroundColor Green }
function Write-Info { param([string]$M) Write-Host ('[INFO] ' + $M) -ForegroundColor Cyan }
function Write-Warn { param([string]$M) Write-Host ('[WARN] ' + $M) -ForegroundColor Yellow }
function Write-Fail { param([string]$M) Write-Host ('[FAIL] ' + $M) -ForegroundColor Red }

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ---------------------------------------------------------------- 提权重跑
if (-not (Test-Admin)) {
    Write-Warn '当前不是管理员：正在请求提权（请在弹出的 UAC 窗口点"是"）。'
    $extra = @()
    if ($Uninstall)  { $extra += '-Uninstall' }
    if ($AtStartup)  { $extra += '-AtStartup' }
    $extra += '-AppPort'
    $extra += "$AppPort"
    $extra += '-SensorPort'
    $extra += "$SensorPort"
    $argLine = '-ExecutionPolicy Bypass -NoProfile -File "{0}" {1}' -f $MyInvocation.MyCommand.Path, ($extra -join ' ')
    try {
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $argLine -WindowStyle Normal
        Write-Info '已拉起提权窗口，请在新窗口里查看结果。'
    } catch {
        Write-Fail ('提权失败：' + $_.Exception.Message)
        Write-Info ('也可以自己用管理员 PowerShell 执行：' + $argLine)
    }
    exit 0
}

Write-Host ''
Write-Host '============================================================' -ForegroundColor DarkCyan
Write-Host '  原生部署 · 开机自启（任务计划程序）' -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor DarkCyan
Write-Info ('任务名称：' + $script:TaskName)
Write-Info ('启动脚本：' + $script:StartScript)

if (-not (Test-Path $script:StartScript)) {
    Write-Fail ('找不到启动脚本：' + $script:StartScript)
    exit 1
}

# ---------------------------------------------------------------- 反注册
if ($Uninstall) {
    $task = Get-ScheduledTask -TaskName $script:TaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        Write-Warn ('计划任务不存在，无需卸载：' + $script:TaskName)
        exit 0
    }
    try {
        Unregister-ScheduledTask -TaskName $script:TaskName -Confirm:$false -ErrorAction Stop
        Write-Ok ('已删除计划任务：' + $script:TaskName)
    } catch {
        Write-Fail ('删除失败：' + $_.Exception.Message)
        exit 1
    }
    Write-Info '现在开机/登录不会再自动拉起服务。'
    exit 0
}

# ---------------------------------------------------------------- 注册
$taskArgs = '-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File "{0}" -AppPort {1} -SensorPort {2}' -f `
    $script:StartScript, $AppPort, $SensorPort

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $taskArgs -WorkingDirectory $script:RepoRoot

if ($AtStartup) {
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    Write-Info '模式：系统启动时自动运行（账户 SYSTEM，不依赖任何人登录）。'
} else {
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
    Write-Info '模式：用户登录时自动运行（账户 ' + $env:USERNAME + '）。'
}

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

$description = '物流企业碳感知智能决策系统：自动启动原生部署（Streamlit 决策台 + Flask 传感器网关）。由 deploy\windows\install-services.ps1 注册。'

try {
    Register-ScheduledTask -TaskName $script:TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Description $description -Force -ErrorAction Stop | Out-Null
    Write-Ok ('已注册计划任务：' + $script:TaskName)
} catch {
    Write-Warn ('Register-ScheduledTask 失败，改用 schtasks.exe：' + $_.Exception.Message)
    $schTask = '/Create'
    $schArgs = @('/TN', $script:TaskName, '/TR', ('powershell.exe ' + $taskArgs), '/SC', 'ONLOGON', '/F')
    if ($AtStartup) { $schArgs = @('/TN', $script:TaskName, '/TR', ('powershell.exe ' + $taskArgs), '/SC', 'ONSTART', '/RU', 'SYSTEM', '/F') }
    $out = & schtasks.exe @schArgs 2>&1
    foreach ($line in ($out | Out-String).Trim() -split "`r?`n") {
        if ($line.Trim()) { Write-Info ('  schtasks：' + $line.Trim()) }
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Fail ('schtasks 注册失败（exit ' + $LASTEXITCODE + '）。')
        exit 1
    }
    Write-Ok ('已通过 schtasks 注册：' + $script:TaskName)
}

# ---------------------------------------------------------------- 结果核对
$task = Get-ScheduledTask -TaskName $script:TaskName -ErrorAction SilentlyContinue
if ($null -ne $task) {
    Write-Info ('当前状态：' + $task.State)
    Write-Info ('下次运行：' + (Get-ScheduledTaskInfo -TaskName $script:TaskName).NextRunTime)
}
Write-Host ''
Write-Ok '开机自启配置完成。'
Write-Info '立即测试一次：schtasks /Run /TN "' + $script:TaskName + '"'
Write-Info '查看任务状态：schtasks /Query /TN "' + $script:TaskName + '" /V /FO LIST'
Write-Info '卸载：powershell -ExecutionPolicy Bypass -File deploy\windows\install-services.ps1 -Uninstall'
Write-Warn '计划任务用的是「隐藏窗口」模式，跑起来没有控制台；日志看 logs\app.err.log 与 logs\sensor.err.log。'
exit 0
