<#
    物流企业碳感知智能决策系统 · Windows 部署总入口（包装层）

    三种模式：
        （无参数）      交互式向导：检测 Docker 可用性，问你走 Docker 还是原生路线
        -Native        直接走原生部署（不依赖 Docker）
        -Finish        Docker 重启后收尾：验证 docker → 校验 compose → build → up → 健康检查 → 冒烟脚本

    用法：
        powershell -ExecutionPolicy Bypass -File deploy\setup_windows.ps1
        powershell -ExecutionPolicy Bypass -File deploy\setup_windows.ps1 -Native
        powershell -ExecutionPolicy Bypass -File deploy\setup_windows.ps1 -Finish

    纪律：
        · **不自己实现 Docker 安装**：Docker 分支一律转调 deploy\install_docker.ps1（父会话所有）。
        · 本脚本默认**不需要管理员**；只有 install_docker.ps1 / firewall.ps1 / install-services.ps1 才提权。
#>
param(
    [switch]$Finish,
    [switch]$Native,
    [int]$AppPort = 8501,
    [int]$SensorPort = 5000,
    [switch]$SkipSmoke
)

$ErrorActionPreference = 'Continue'

$script:RepoRoot = Split-Path -Parent $PSScriptRoot
$script:WindowsDir = Join-Path $PSScriptRoot 'windows'
$script:NativeScript = Join-Path $script:WindowsDir 'start-native.ps1'
$script:StopScript = Join-Path $script:WindowsDir 'stop-native.ps1'
$script:InstallDocker = Join-Path $PSScriptRoot 'install_docker.ps1'
$script:SmokeDir = Join-Path $script:RepoRoot '_shots'

function Write-Ok   { param([string]$M) Write-Host ('[OK]   ' + $M) -ForegroundColor Green }
function Write-Info { param([string]$M) Write-Host ('[INFO] ' + $M) -ForegroundColor Cyan }
function Write-Warn { param([string]$M) Write-Host ('[WARN] ' + $M) -ForegroundColor Yellow }
function Write-Fail { param([string]$M) Write-Host ('[FAIL] ' + $M) -ForegroundColor Red }
function Write-Step { param([string]$M) Write-Host ('[====] ' + $M) -ForegroundColor Magenta }

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
        } catch { }
        Start-Sleep -Seconds 3
    }
    return $false
}

function Test-DockerAvailable {
    $cmd = Get-Command docker -ErrorAction SilentlyContinue
    if ($null -eq $cmd) { return $false }
    $out = & docker version --format '{{.Server.Version}}' 2>&1
    if ($LASTEXITCODE -ne 0) { return $false }
    return $true
}

function Invoke-NativeRoute {
    Write-Step '原生部署路线'
    Write-Info ('启动脚本：' + $script:NativeScript)
    & $script:NativeScript -AppPort $AppPort -SensorPort $SensorPort
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        Write-Warn ('原生启动脚本返回 ' + $code + '（可能是端口被占用或健康检查超时）。')
    }
    $lanIp = Get-LanIPv4
    Write-Host ''
    Write-Host '  下一步（可选）' -ForegroundColor Cyan
    Write-Host '   · 放行内网防火墙（需管理员）：powershell -ExecutionPolicy Bypass -File deploy\windows\firewall.ps1'
    Write-Host '   · 开机自启（需管理员）    ：powershell -ExecutionPolicy Bypass -File deploy\windows\install-services.ps1'
    Write-Host '   · 停止服务                ：powershell -ExecutionPolicy Bypass -File deploy\windows\stop-native.ps1'
    Write-Host ('   · 决策台：http://' + $lanIp + ':' + $AppPort + '    传感器：http://' + $lanIp + ':' + $SensorPort + '/api/upload_sensor') -ForegroundColor DarkGray
    return $code
}

function Invoke-DockerInstall {
    Write-Step 'Docker 路线 · 安装运行环境'
    if (-not (Test-Path $script:InstallDocker)) {
        Write-Fail ('找不到安装脚本：' + $script:InstallDocker)
        return 1
    }
    Write-Info '转调父会话的安装脚本（它会自己弹 UAC 提权）：'
    Write-Info ('  ' + $script:InstallDocker)
    & $script:InstallDocker
    Write-Host ''
    Write-Warn '启用虚拟机平台后需要**重启一次**才生效（脚本默认不自动重启）。'
    Write-Info '重启后执行：powershell -ExecutionPolicy Bypass -File deploy\setup_windows.ps1 -Finish'
    Write-Info '不想重启也能先跑原生路线：powershell -ExecutionPolicy Bypass -File deploy\setup_windows.ps1 -Native'
    return 0
}

function Invoke-DockerFinish {
    Write-Step 'Docker 路线 · 收尾验证'
    Set-Location $script:RepoRoot

    # ① docker version
    $cmd = Get-Command docker -ErrorAction SilentlyContinue
    if ($null -eq $cmd) {
        Write-Fail '找不到 docker 命令。请先运行：powershell -ExecutionPolicy Bypass -File deploy\install_docker.ps1'
        return 1
    }
    $out = & docker version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Fail 'docker 命令存在但守护进程连不上（Docker Desktop 没启动 / WSL2 没生效）。'
        Write-Info '  排查：启动 Docker Desktop → 或改走原生路线 deploy\setup_windows.ps1 -Native'
        return 1
    }
    Write-Ok 'docker version 正常'

    # ② compose 语法校验
    $composeArgs = @('compose')
    if (Test-Path (Join-Path $PSScriptRoot 'env')) { $composeArgs += @('--env-file', (Join-Path $PSScriptRoot 'env')) }
    $null = & docker @composeArgs config -q 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Fail 'docker compose config 校验不通过（compose 语法或变量有误）。'
        return 1
    }
    Write-Ok 'docker compose config 校验通过'

    # ③ build
    Write-Info '构建镜像（首次较慢，请耐心等待）…'
    & docker @composeArgs build
    if ($LASTEXITCODE -ne 0) {
        Write-Fail 'docker compose build 失败。'
        return 1
    }
    Write-Ok '镜像构建完成'

    # ④ up -d
    & docker @composeArgs up -d
    if ($LASTEXITCODE -ne 0) {
        Write-Fail 'docker compose up -d 失败。'
        return 1
    }
    Write-Ok '容器已启动'

    # ⑤ 健康检查
    Write-Info '健康检查中（最长 120 秒）…'
    $appOk = Wait-HttpHealthy -Url 'http://127.0.0.1:8501/_stcore/health' -TimeoutSec 120
    if ($appOk) { Write-Ok '决策台健康（/_stcore/health = 200）' } else { Write-Fail '决策台健康检查超时：docker compose logs app' }
    $sensorOk = Wait-HttpHealthy -Url 'http://127.0.0.1:5000/api/health' -TimeoutSec 120
    if ($sensorOk) { Write-Ok '传感器网关健康（/api/health = 200）' } else { Write-Fail '传感器网关健康检查超时：docker compose logs sensor' }

    # ⑥ 冒烟脚本
    if ($SkipSmoke) {
        Write-Info '按要求跳过冒烟脚本（-SkipSmoke）。'
    } elseif ($null -eq (Get-Command node -ErrorAction SilentlyContinue)) {
        Write-Warn '没找到 node，跳过 4 个页面冒烟脚本（要装 Node 18+ 才能跑）。'
    } else {
        Write-Info '运行页面冒烟脚本…'
        if (-not (Test-Path $script:SmokeDir)) { New-Item -ItemType Directory -Path $script:SmokeDir -Force | Out-Null }
        $smokes = @('verify_pages.js', 'verify_nav.js', 'verify_sliders.js', 'verify_dashboard_layout.js')
        foreach ($s in $smokes) {
            $scriptPath = Join-Path $script:RepoRoot ('tools\' + $s)
            if (-not (Test-Path $scriptPath)) {
                Write-Warn ('冒烟脚本不存在，跳过：' + $s)
                continue
            }
            Write-Host ('--- ' + $s + ' ---')
            & node $scriptPath 'http://localhost:8501' $script:SmokeDir
            if ($LASTEXITCODE -eq 0) {
                Write-Ok ($s + ' 通过')
            } else {
                Write-Warn ($s + ' 未全部通过（exit ' + $LASTEXITCODE + '），截图见 ' + $script:SmokeDir)
            }
        }
    }

    # ⑦ 访问地址与证书提示
    $lanIp = Get-LanIPv4
    Write-Host ''
    Write-Host '------------------------------------------------------------' -ForegroundColor DarkCyan
    Write-Host '  访问地址' -ForegroundColor Cyan
    Write-Host '------------------------------------------------------------' -ForegroundColor DarkCyan
    Write-Host ("  决策台（HTTP）  ：http://" + $lanIp + ":8501")
    Write-Host ("  决策台（HTTPS） ：https://" + $lanIp + ":8443")
    Write-Host ("  ESP32 接入点    ：https://" + $lanIp + ":8443/api/upload_sensor")
    Write-Host ("  自动跳转 HTTP   ：http://" + $lanIp + ":8080  →  8443")
    Write-Host '------------------------------------------------------------' -ForegroundColor DarkCyan
    Write-Warn '8443/8080 用的是 Caddy **内网自签 CA**：首次访问浏览器会报证书不受信任。'
    Write-Info '  信任办法：浏览器访问 https://<主机>:8443 → 高级 → 继续访问；'
    Write-Info '  或把 Caddy 的根证书导入到「受信任的根证书颁发机构」后重新打开浏览器。'
    Write-Info '  ESP32 侧：把根 CA 烧进固件并校时（见 hardware/esp32 说明）。'
    Write-Info '查看容器状态：docker compose ps    日志：docker compose logs -f app'

    if (-not $appOk -or -not $sensorOk) { return 1 }
    return 0
}

# ================================================================ 入口
Write-Host ''
Write-Host '============================================================' -ForegroundColor DarkCyan
Write-Host '  物流企业碳感知智能决策系统 · Windows 部署向导' -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor DarkCyan
Write-Info ('仓库根目录：' + $script:RepoRoot)

if ($Finish) {
    $code = Invoke-DockerFinish
    exit $code
}

if ($Native) {
    $code = Invoke-NativeRoute
    exit $code
}

# ---------------------------------------------------------------- 交互式向导
Write-Host ''
Write-Step '环境探测'
$dockerReady = Test-DockerAvailable
if ($dockerReady) {
    Write-Ok '检测到 Docker 可用（docker 命令 + 守护进程都在）。'
} else {
    $hasDockerCmd = $null -ne (Get-Command docker -ErrorAction SilentlyContinue)
    if ($hasDockerCmd) {
        Write-Warn '有 docker 命令，但守护进程连不上（Docker Desktop 没启动 / WSL2 没生效 / 嵌套虚拟化没开）。'
    } else {
        Write-Warn '本机没有 docker 命令。'
    }
}

Write-Host ''
Write-Host '  两条路线可选：' -ForegroundColor Cyan
Write-Host '   [1] Docker 路线：环境一致、依赖打包、TLS 网关齐全；需要 WSL2 + Docker Desktop，' -ForegroundColor Gray
Write-Host '                    在 Hyper-V 虚拟机里可能因嵌套虚拟化缺失而跑不起来。' -ForegroundColor Gray
Write-Host '   [2] 原生路线  ：本机 Python 直接起，今天就能用；无容器隔离，明文 HTTP + 内网防火墙。' -ForegroundColor Gray
Write-Host ''

if ($dockerReady) {
    $answer = Read-Host '请选择路线（1=Docker，2=原生，默认 2）'
    if ($null -eq $answer -or $answer.Trim() -eq '') { $answer = '2' }
} else {
    Write-Warn '**推荐原生路线**：本机在虚拟机里，嵌套虚拟化不一定可用，Docker 路线可能装不上。'
    $answer = Read-Host '直接用原生路线吗？（Y=原生，N=先试 Docker 安装，默认 Y）'
    if ($null -eq $answer -or $answer.Trim() -eq '') { $answer = 'Y' }
}

if ($answer.Trim().ToUpper() -eq '1') {
    $code = Invoke-DockerInstall
    exit $code
}

$code = Invoke-NativeRoute
exit $code
