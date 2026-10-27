<#
  Docker Desktop 安装包装（提权执行，记录日志）。

  由 deploy/install_docker.ps1 或人工调用：
      powershell -ExecutionPolicy Bypass -File deploy\install_docker_wrapper.ps1 -Installer <路径>
#>
param(
  [Parameter(Mandatory = $true)][string]$Installer,
  [string]$LogPath = 'D:\oceanbase01\2027-challenge-cup\_shots\docker_install.log',
  [string]$Backend = 'wsl-2'
)

$ErrorActionPreference = 'Continue'

function Write-Log {
  param([string]$Message, [string]$Level = 'INFO')
  $line = "[{0}] [{1}] {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
  Write-Host $line
  Add-Content -Path $LogPath -Value $line -Encoding UTF8
}

if (-not (Test-Path $Installer)) {
  Write-Log ("找不到安装包：{0}" -f $Installer) 'FAIL'
  exit 1
}

$sizeMb = [math]::Round((Get-Item $Installer).Length / 1MB, 1)
Write-Log ("开始安装 Docker Desktop（{0} MB，backend={1}）…" -f $sizeMb, $Backend) 'INFO'

# Docker Desktop 安装器：install / --quiet / --accept-license / --backend=wsl-2
$args = @('install', '--quiet', '--accept-license', ("--backend=" + $Backend))
Write-Log ("执行：{0} {1}" -f $Installer, ($args -join ' ')) 'INFO'

$proc = Start-Process -FilePath $Installer -ArgumentList $args -Wait -PassThru
$code = $proc.ExitCode
Write-Log ("安装器退出码：{0}" -f $code) 'INFO'

if (Test-Path "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe") {
  Write-Log 'Docker Desktop.exe 已就位。' 'OK'
} else {
  Write-Log '没找到 Docker Desktop.exe——安装未完成。' 'FAIL'
}

Write-Log '注意：WSL2 与虚拟机平台刚启用，**需要重启**后 Docker 才能启动。' 'WARN'
Write-Log '重启后执行：powershell -ExecutionPolicy Bypass -File deploy\setup_windows.ps1 -Finish' 'INFO'
exit $code
