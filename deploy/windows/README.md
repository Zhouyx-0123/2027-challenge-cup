# Windows 原生部署手册（不依赖 Docker）

> 适用仓库：物流企业碳感知智能决策系统（Streamlit 决策台 + Flask 传感器网关）
> 适用场景：**Docker 装不上 / 跑不起来**的 Windows 机器（典型：本机跑在 Hyper-V 虚拟机里，
> 宿主没透传嵌套虚拟化，WSL2 与 Docker Desktop 会报 `0x80370102`）。

原生路线的目标：**今天就能用**。代价是没有容器隔离、没有内置 TLS 网关，靠「明文 + 内网 +
防火墙限 LocalSubnet」把风险收在内网里。

---

## 1. 两条路线对比

| 维度 | Docker 路线 | 原生路线（本目录） |
| --- | --- | --- |
| 前置依赖 | WSL2 + 虚拟机平台 + Docker Desktop（需管理员、需重启） | Python ≥ 3.10 + `requirements.txt` 依赖 |
| 启动命令 | `docker compose up -d --build` | `powershell -ExecutionPolicy Bypass -File deploy\windows\start-native.ps1` |
| 端口 | 8501 / 5000 / 8443 / 8080（Caddy） | 8501 / 5000（只这两个） |
| 开机自启 | 容器 `restart: unless-stopped`，Docker Desktop 随系统启动 | 任务计划程序 `CarbonTide Native`（需管理员） |
| TLS | 内置 Caddy，自签 CA，8443 HTTPS / 8080 跳转 | **无**，默认明文 HTTP（可另装 Windows 版 Caddy / IIS 反代） |
| 依赖隔离 | 镜像内固定版本，换机一致 | 用宿主 Python 环境，换机要重装依赖 |
| 升级 | `docker compose build && docker compose up -d`，旧镜像可回滚 | 停 → 换代码 → 起（`git pull` 后重启进程），回滚＝切回旧代码再重启 |
| 回滚粒度 | 镜像 tag | 代码版本（git checkout / 备份目录） |
| 适用场景 | 正式演示、比赛现场、CI 一致性 | Docker 装不上时的**唯一可用路径**；本机快速验证 |
| 管理员权限 | 安装阶段需要 | 启动/停止**不需要**；防火墙、开机自启才需要 |

---

## 2. 原生路线：一步步操作

### 2.1 前置条件

- Python **3.10 或更高**（本机实测 3.12.6 可用），安装时勾选 *Add python.exe to PATH*。
- 依赖装好：

```powershell
pip install -r requirements.txt
```

- 离线环境优先用仓库里的 `wheels/`（若已备好）：

```powershell
pip install --no-index --find-links=wheels -r requirements.txt
```

- 确认关键依赖齐全（`flask-cors` 缺失只会导致跨域头不下发，服务照常起）：

```powershell
python -c "import streamlit, pandas, pyarrow, flask; print('ok')"
```

### 2.2 一条命令起服务（不需要管理员）

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\start-native.ps1
```

常用变体：

```powershell
# 只起决策台（不跑传感器网关）
powershell -ExecutionPolicy Bypass -File deploy\windows\start-native.ps1 -NoSensor

# 换端口（8501 被占用时）
powershell -ExecutionPolicy Bypass -File deploy\windows\start-native.ps1 -AppPort 8511 -SensorPort 5051

# 前台跑（排障：日志直接打屏，Ctrl+C 停止）
powershell -ExecutionPolicy Bypass -File deploy\windows\start-native.ps1 -Foreground
```

脚本会依次做：找 Python（≥3.10）→ 查依赖 → 查端口占用 → 拉起两个进程 →
**核对端口归属**（确认监听者确实是刚启动的那个 PID）→ 轮询健康检查 → 写
`logs\native.pids.json` → 打印局域网访问地址。

> 脚本**幂等**：已在跑就打印地址后退出，不会重复起。
> 端口被**别的**进程占用时只提示、**绝不杀**别人的进程（除非你显式 `-Force`）。

**关于日志与启动器**：`logs\` 下除了日志，还有脚本自动生成的
`app_launcher.py` / `sensor_launcher.py` 两个启动器。它们只做两件事：

1. 把 `stdout/stderr` 接到日志文件（**追加**模式，多次启动会累积，便于回看历史）；
2. 让服务进程**不继承调用方的 stdout 管道**——否则 `start-native.ps1 | Tee-Object`
   会因为服务进程握着管道写端而一直不返回（看起来像卡死）。

服务进程本身仍然是普通的 `python -m streamlit run app/app.py` / `python server.py`，
业务代码零改动。

### 2.3 验证

```powershell
# 决策台
curl http://127.0.0.1:8501/_stcore/health          # 期望：ok
# 传感器网关
curl http://127.0.0.1:5000/api/health              # 期望：{"status":"success",...}
# 页面级冒烟（需要 Node 18+）
node tools/verify_pages.js http://localhost:8501 _shots
```

### 2.4 设备接入地址（ESP32）

启动脚本打印的「传感器接入点」就是 ESP32 要 POST 的地址：

```
http://<本机局域网 IP>:5000/api/upload_sensor
```

例如本机是 `10.104.105.152`，则 ESP32 里写：

```cpp
const char* SERVER = "http://10.104.105.152";
// POST /api/upload_sensor
```

注意：

- 传感器接口默认**开启鉴权**（`SENSOR_AUTH=1`，fail-closed）：请求头必须带
  `X-Device-Token`，且设备要先登记到 `config/devices.json`（用
  `tools/device_tokens.py` 生成令牌）。
- 局域网 IP 会变（DHCP）。**推荐在路由器上给本机做 IP 绑定（静态 DHCP）**，否则重启后
  ESP32 要改地址。用 `ipconfig` 查当前 IP；脚本每次启动也会重新打印。
- `server.py` 把端口写死成 5000；`start-native.ps1 -SensorPort 5051` 走的是不改动业务代码的
  等效启动方式（同一个 `app`，换监听端口）。

### 2.5 开机自启（需要管理员）

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\install-services.ps1
```

- 注册成计划任务 **`CarbonTide Native`**（用户登录时运行，`-AtStartup` 可改成系统启动时 + SYSTEM）。
- 脚本自己弹 UAC 提权重跑（和 `deploy\install_docker.ps1` 同一套做法）。
- 检查 / 立即跑一次：

```powershell
schtasks /Query /TN "CarbonTide Native" /V /FO LIST
schtasks /Run /TN "CarbonTide Native"
```

- 卸载：

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\install-services.ps1 -Uninstall
```

> **为什么不用 NSSM**：NSSM 是第三方 exe，要单独下载、常被杀毒软件误报、卸载还得再下一个工具；
> 计划任务是 Windows 自带能力，注册一条命令、卸载一条命令，出问题还能用 `schtasks` 直接查。

### 2.6 防火墙（需要管理员）

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\firewall.ps1
```

- 放行 **8501 / 5000 入站 TCP**，并且限定 `-RemoteAddress LocalSubnet`（**只放内网，不开公网**）。
- 换端口：`-Ports 8511,5051`；撤销：`-Remove`。

> ！！ 安全提醒 ！！
> · ESP32 与本机必须处在**同一网段**（同一路由器 / 同一交换机 VLAN）。
> · 5000 是设备写入接口，**绝不要暴露到公网**；需要跨网段访问请走 VPN，
>   不要把规则的 `RemoteAddress` 改成 `Any`。

### 2.7 升级

```powershell
# 1) 停
powershell -ExecutionPolicy Bypass -File deploy\windows\stop-native.ps1
# 2) 换代码（例如 git pull / 拷贝新版本目录）
# 3) 起
powershell -ExecutionPolicy Bypass -File deploy\windows\start-native.ps1
# 4) 验证
curl http://127.0.0.1:8501/_stcore/health
curl http://127.0.0.1:5000/api/health
```

依赖有变化时，先补依赖再起：`pip install -r requirements.txt`。

### 2.8 回滚

原生路线没有镜像可回滚，回滚 = **把代码换回旧版本再重启**：

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\stop-native.ps1
git checkout <上一个可用 tag 或 commit>       # 或从备份目录还原
powershell -ExecutionPolicy Bypass -File deploy\windows\start-native.ps1
```

> 想保留「新旧两套随时切换」，升级前先把整个仓库复制一份到
> `D:\oceanbase01\carbontide-prev\`（含 `.venv` 的话体积大，只复制代码 + 记下依赖版本即可）。

---

## 3. 关于 TLS

原生方案**默认明文 HTTP**。这是有意的取舍：内网演示 + 防火墙限 `LocalSubnet` +
设备接口自带令牌鉴权，风险可控；换成自签证书反而会让 ESP32 那边更麻烦（要烧 CA、要校时）。

需要 HTTPS 时，两个可选做法（要点，不展开）：

**A. 装 Windows 版 Caddy**（和容器方案同一套思路）

```powershell
# 下载 caddy-windows-amd64.zip，解压到 D:\caddy\
D:\caddy\caddy.exe run --config deploy\caddy\Caddyfile
```

把 `deploy/caddy/Caddyfile` 里的 `localhost` 换成内网 IP、证书指向本机证书文件即可；
Caddy 可同样注册成计划任务（`caddy.exe run`）。

**B. 用 IIS 反代**

```powershell
# 安装 IIS（需管理员）
Enable-WindowsOptionalFeature -Online -FeatureName IIS-WebServerRole, IIS-WebServer
```

在 IIS 管理器里建站点 → 绑定 443 → 安装「URL 重写 + Application Request Routing」→
反代到 `http://127.0.0.1:8501` / `http://127.0.0.1:5000`；证书走 IIS 的「服务器证书」向导。

两种做法都要记得**放行对应端口（限 LocalSubnet）**，并让 ESP32 侧信任同一个根 CA。

---

## 4. 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `[FAIL] 端口 8501 已被占用（PID xxxx）` | 端口被别的程序占用 | ① `powershell -ExecutionPolicy Bypass -File deploy\windows\stop-native.ps1 -Force`（**会杀掉占用端口的任何进程**，确认后再用）；② 或换端口 `-AppPort 8511` |
| `[FAIL] 找不到可用的 Python（>= 3.10）` | PATH 里没有 python | 装 Python 3.10+ 并勾 *Add to PATH*，或重开终端；也可以用 `py -3` 启动器（脚本已支持） |
| `[FAIL] 依赖缺失：streamlit / pandas / pyarrow / flask` | 依赖没装全 | `pip install -r requirements.txt`；离线环境 `pip install --no-index --find-links=wheels -r requirements.txt` |
| `[WARN] 缺少 flask-cors` | 只影响浏览器跨域头 | 服务照常可用；要补：`pip install flask-cors` |
| `[FAIL] 决策台启动后没能监听端口` | 端口被抢 / 端口已在用 / 依赖导入报错 | 看 `logs\app.err.log`；`logs\sensor.err.log` 同理 |
| ESP32 连不上 | ① 不同网段 ② 防火墙没放行 ③ AP 地址变了 ④ 缺 `X-Device-Token` ⑤ 设备没登记 | 逐项查：ESP32 与本机 IP 是否同段 → 跑 `firewall.ps1` → 核对打印出来的局域网 IP → 看 `logs\sensor.out.log` 里的 401/403 → `config/devices.json` |
| 局域网访问打不开、但 `127.0.0.1` 正常 | 防火墙 | 跑 `deploy\windows\firewall.ps1`；或临时用 `New-NetFirewallRule ... -RemoteAddress LocalSubnet` 手工加一条 |
| 开机后服务没起来 | 计划任务没注册 / 用户没登录 | `schtasks /Query /TN "CarbonTide Native"`；`-AtStartup` 模式不依赖登录 |
| 端口被占但 `netstat` 看不到 | 残留的孤儿进程 | `Get-NetTCPConnection -LocalPort 8501 | Select-Object OwningProcess` 看 PID，再 `Stop-Process -Id <PID> -Force` |

### 看日志

```powershell
Get-Content logs\app.err.log     -Tail 50   # 决策台错误
Get-Content logs\app.out.log    -Tail 50   # 决策台输出
Get-Content logs\sensor.err.log  -Tail 50   # 传感器错误
Get-Content logs\sensor.out.log -Tail 50   # 传感器访问日志
Get-Content logs\native.pids.json           # 当前记录的 PID / 端口
```

日志是**追加**写的（同一路径可跨多次启动累积）；想从头看就先把文件改名或清空。
`logs/` 是运行时目录，**不进版本库**（`.gitignore` 里加 `logs/`）。

---

## 5. 本目录脚本清单

| 脚本 | 需要管理员 | 职责 |
| --- | --- | --- |
| `start-native.ps1` | 否 | 起决策台 + 传感器；环境自检、端口占用判断、健康检查、记 PID、打印访问地址 |
| `stop-native.ps1` | 否 | 按 PID 文件停服务；没有 PID 文件时按端口找占用者（**默认不杀**，`-Force` 才杀） |
| `install-services.ps1` | **是**（自提权） | 注册 / 反注册计划任务 `CarbonTide Native`（开机自启） |
| `firewall.ps1` | **是**（自提权） | 放行 8501 / 5000 入站（限 `LocalSubnet`）；`-Remove` 撤销 |
| `../setup_windows.ps1` | 否（内部转调提权脚本） | 总入口：向导 / `-Native` / `-Finish` 三种模式 |

顶层入口（推荐用这个）：

```powershell
powershell -ExecutionPolicy Bypass -File deploy\setup_windows.ps1            # 交互式向导
powershell -ExecutionPolicy Bypass -File deploy\setup_windows.ps1 -Native    # 直接原生部署
powershell -ExecutionPolicy Bypass -File deploy\setup_windows.ps1 -Finish    # Docker 重启后的收尾验证
```
