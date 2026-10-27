<#
    物流企业碳感知智能决策系统 · Windows 防火墙放行（内网限定）

    用 Windows 自带的 New-NetFirewallRule 放行 8501（决策台）与 5000（传感器网关），
    **只放行 LocalSubnet（同一网段）**，不向公网开放。

    用法：
        powershell -ExecutionPolicy Bypass -File deploy\windows\firewall.ps1
        powershell -ExecutionPolicy Bypass -File deploy\windows\firewall.ps1 -Ports 8501,5000
        powershell -ExecutionPolicy Bypass -File deploy\windows\firewall.ps1 -Remove

    参数：
        -Ports    要放行的端口（默认 8501,5000）
        -Remove   撤销规则

    纪律：
        · **需要管理员权限**（脚本自己会 Start-Process -Verb RunAs 弹 UAC 提权重跑）。
        · 绝不使用 -RemoteAddress Any：5000 是设备写入接口，绝不能暴露到公网。
#>
param(
    [int[]]$Ports = @(8501, 5000),
    [switch]$Remove
)

$ErrorActionPreference = 'Continue'

function Write-Ok   { param([string]$M) Write-Host ('[OK]   ' + $M) -ForegroundColor Green }
function Write-Info { param([string]$M) Write-Host ('[INFO] ' + $M) -ForegroundColor Cyan }
function Write-Warn { param([string]$M) Write-Host ('[WARN] ' + $M) -ForegroundColor Yellow }
function Write-Fail { param([string]$M) Write-Host ('[FAIL] ' + $M) -ForegroundColor Red }

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-RuleName {
    param([int]$Port)
    return ('CarbonTide Inbound TCP ' + $Port)
}

# ---------------------------------------------------------------- 提权重跑
if (-not (Test-Admin)) {
    Write-Warn '当前不是管理员：正在请求提权（请在弹出的 UAC 窗口点"是"）。'
    $extra = @()
    if ($Remove) { $extra += '-Remove' }
    $extra += '-Ports'
    $extra += ($Ports -join ',')
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
Write-Host '  原生部署 · 防火墙放行（限内网 LocalSubnet）' -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor DarkCyan

$failed = 0
foreach ($p in $Ports) {
    $ruleName = Get-RuleName -Port $p
    $exists = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue

    if ($Remove) {
        if ($null -eq $exists) {
            Write-Info ('规则不存在，跳过：' + $ruleName)
            continue
        }
        try {
            Remove-NetFirewallRule -Name $ruleName -ErrorAction Stop
            Write-Ok ('已删除防火墙规则：' + $ruleName)
        } catch {
            Write-Fail ('删除失败：' + $ruleName + '（' + $_.Exception.Message + '）')
            $failed++
        }
        continue
    }

    # 幂等：存在就先删再建，保证策略与参数一致
    if ($null -ne $exists) {
        try {
            Remove-NetFirewallRule -Name $ruleName -ErrorAction Stop
        } catch {
            Write-Warn ('旧规则删除失败，将尝试直接更新：' + $_.Exception.Message)
        }
    }

    try {
        New-NetFirewallRule -Name $ruleName `
            -DisplayName ('物流碳感知系统 · 内网放行 TCP ' + $p) `
            -Description '只允许同一局域网网段访问（RemoteAddress=LocalSubnet），不对公网开放。' `
            -Direction Inbound -Action Allow -Protocol TCP -LocalPort $p `
            -Profile Private,Domain -RemoteAddress LocalSubnet -Enabled True -ErrorAction Stop | Out-Null
        Write-Ok ('已放行内网入站 TCP ' + $p + '（RemoteAddress=LocalSubnet）')
    } catch {
        Write-Fail ('新建规则失败 TCP ' + $p + '：' + $_.Exception.Message)
        $failed++
    }
}

Write-Host ''
if ($Remove) {
    Write-Ok '防火墙规则已撤销。'
} elseif ($failed -eq 0) {
    Write-Ok '防火墙放行完成。'
    Write-Info '核对：Get-NetFirewallRule -Name "CarbonTide Inbound TCP 8501" | Get-NetFirewallPortFilter'
} else {
    Write-Fail ('有 ' + $failed + ' 条规则没成功，请用管理员 PowerShell 重试。')
}

Write-Host ''
Write-Host '  ！！ 安全提醒 ！！' -ForegroundColor Red
Write-Host '   · ESP32 与本机必须处在**同一网段**（同一路由器 / 同一交换机 VLAN）。' -ForegroundColor Yellow
Write-Host '   · 5000 是设备写入接口（SENSOR_AUTH 默认开启鉴权），**绝不要暴露到公网**。' -ForegroundColor Yellow
Write-Host '   · 本方案只放行 LocalSubnet；如需跨网段访问，请走 VPN，不要把 RemoteAddress 改成 Any。' -ForegroundColor Yellow
Write-Host '' -ForegroundColor Yellow
exit ($(if ($failed -eq 0) { 0 } else { 1 }))
