# 物流企业碳感知智能决策系统 · 容器化部署手册

本文面向**部署与运维人员**，说明如何用 Docker + docker compose + Caddy 把本系统交付到一台内网服务器上，
并在局域网内以 HTTPS 暴露给评审人员与 ESP32 设备。

- 应用代码与判定口径：本仓库 `app/`（Streamlit 决策台）、`src/`（计算层）、`server.py`（传感器网关）。
- 交付物：`Dockerfile`（镜像）、`docker-compose.yml`（编排）、`deploy/caddy/Caddyfile`（内网 TLS 网关）、`deploy/env.example`（变量样例）。

---

## 1. 架构一览

```
                      内网客户端 / ESP32
                             │  https://<主机>:8443
                             ▼
                 ┌───────────────────────────┐
                 │  caddy (caddy:2-alpine)   │  :8443 HTTPS（内网自签 CA）
                 │  /api/*  → sensor:5000    │  :8080 HTTP → 308 跳 HTTPS
                 │  其它    → app:8501        │
                 └───────────┬───────────────┘
             ┌───────────────┴───────────────┐
             ▼                               ▼
   ┌─────────────────────┐        ┌─────────────────────┐
   │ app（Streamlit）    │        │ sensor（Flask）     │
   │ :8501  五页决策台   │        │ :5000  /api/*       │
   └──────────┬──────────┘        └──────────┬──────────┘
              └───────────────┬───────────────┘
                              ▼
        ./data（结果表 + 原始数据） ./config:ro（因子 + 密钥）
        ./outputs（审计产物）        ./backups（备份）
```

同一个镜像跑两个服务：`app` 用 `streamlit run`，`sensor` 用 `python server.py`。
镜像里**不含**任何密钥文件，也**不含** `data/raw/`。

---

## 2. 前置条件

| 项目 | 要求 |
| --- | --- |
| Docker Engine | ≥ 24.0 |
| Docker Compose | v2（`docker compose version` 能打印版本号） |
| 磁盘 | ≥ 6 GB（镜像约 1.5–2 GB，含 CJK 字体与 pandas/pyarrow 依赖） |
| 内存 | ≥ 2 GB（Streamlit 首屏加载 parquet 结果表） |
| 端口 | 8501 / 5000 / 8080 / 8443 未被占用（可用 `deploy/env` 改） |
| 仓库 | 本仓库完整 clone（`data/processed` 的结果表随仓库入库） |

---

## 3. 启动步骤

### 3.1 准备变量

```bash
cd <仓库根目录>
cp deploy/env.example deploy/env      # deploy/env 不要提交进 git
# 编辑 deploy/env，填入高德 / DeepSeek 密钥；不填也能起，页面相应功能降级
```

`deploy/env` 是 compose 的插值来源，用 `--env-file` 指定：

```bash
docker compose --env-file deploy/env config     # 先干跑一遍，看变量有没有被正确读进来
```

> 也可以把变量直接放进宿主 shell 环境，或放在仓库根目录的 `.env`（compose 默认自动加载）。
> 三者优先级：shell 环境变量 > `--env-file` 指定文件 > compose 内置默认值。

### 3.2 构建镜像

```bash
docker compose --env-file deploy/env build
docker images carbontide:latest
```

### 3.3 启动

```bash
docker compose --env-file deploy/env up -d
docker compose ps
```

`caddy` 依赖 `app` 与 `sensor` 都健康后才启动，所以第一次 `up` 后约 30–60 秒 Caddy 才会开始监听 8443。

### 3.4 停止 / 清理

```bash
docker compose stop            # 停容器，保留镜像与数据
docker compose down            # 删容器与网络，保留数据卷与镜像
docker compose down -v         # 连 Caddy 的证书卷一起删（会导致 CA 变化，客户端要重新信任）
```

---

## 4. 访问与证书信任

### 4.1 入口

| 地址 | 说明 |
| --- | --- |
| `https://<主机IP>:8443` | 统一入口（推荐，评审人员走这个） |
| `https://<主机IP>:8443/api/upload_sensor` | ESP32 数据上报入口 |
| `http://<主机IP>:8080` | 明文入口，会 308 跳到 8443 |
| `http://<主机IP>:8501` | Streamlit 直连（绕过 TLS，排障用） |

### 4.2 内网自签证书的信任

Caddy 用 `tls internal` 自建内网 CA，浏览器第一次访问会提示「不安全」。两种处理方式：

**方式 A：把 CA 根证书导出并导入客户端信任库（推荐，一次配置，团队通用）**

```bash
# 在部署机上导出根证书
docker compose exec caddy cat /data/caddy/pki/authorities/local/root.crt > root.crt
```

然后把 `root.crt` 分发给客户端：

- **Windows**：双击 →「安装证书」→「本地计算机」→「受信任的根证书颁发机构」；
  或 PowerShell（管理员）：
  ```powershell
  Import-Certificate -FilePath .\root.crt -CertStoreLocation Cert:\LocalMachine\Root
  ```
- **macOS**：`sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain root.crt`
- **Linux**（Debian/Ubuntu）：`sudo cp root.crt /usr/local/share/ca-certificates/carbontide.crt && sudo update-ca-certificates`
- **ESP32 / 单片机**：无法导入根证书时，让它**跳过证书校验**（`InsecureSkipVerify`），
  或把根证书烧进设备固件。

> 证书卷 `caddy_data` 是命名卷，容器重建不会换 CA；只有 `docker compose down -v` 才会清掉，
> 之后所有客户端需要重新导入新的 `root.crt`。

**方式 B：只在单台客户端临时信任（Chrome 命令行）**

```bash
# Chrome / Edge：--ignore-certificate-errors 启动即可跳过告警
chrome --ignore-certificate-errors https://<主机IP>:8443
```

> `caddy trust` 命令只能在 Caddy 自己的宿主机上安装根证书，容器内没有该命令的宿主集成，
> 所以本项目统一用上面的「导出 root.crt 再分发」流程。

---

## 5. 密钥注入

镜像里**没有**密钥文件（`config/*_key.txt` 被 `.dockerignore` 挡住，Dockerfile 也只 COPY `config/*.json`）。两种注入方式，任选其一或并用：

### 方式 A：环境变量（推荐，容器重启即生效）

在 `deploy/env` 里填：

```bash
AMAP_WEB_SERVICE_KEY=...
AMAP_JS_API_KEY=...
AMAP_JS_SECURITY_CODE=...
DEEPSEEK_API_KEY=...
```

compose 会把它们注入 `app` 容器。代码里**环境变量优先于文件**（见 `src/routing/amap_client.py`、`src/routing/amap_js.py`、`app/live_services.py`）。

### 方式 B：只读挂载密钥文件

把密钥放在宿主 `config/` 下（`config/` 已被 `.gitignore` 挡住，不会入库），compose 已把 `./config` 只读挂到 `/app/config`：

```
config/
├── amap_key.txt              # 高德 Web 服务 Key
├── amap_js_key.txt           # 高德 JS API Key
├── amap_js_security_code.txt # 高德 JS 安全密钥
├── deepseek_key.txt          # DeepSeek Key
├── calc_factors.json         # 因子表（入库）
└── ...
```

容器内 `/app/config` 是**只读**的，程序不会也不应该往里写。

> ⚠️ `config/` 挂载是 `:ro`，若日志报 `Permission denied` 或写不进去，说明程序试图写配置目录 —— 这属于程序问题，不要把挂载改成可写来绕过。

---

## 6. 目录挂载说明

| 宿主路径 | 容器路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| `./data` | `/app/data` | 读写 | 结果表（`data/processed`）与原始数据（`data/raw`）；**宿主必须已有 `data/processed` 的结果表**，否则会遮蔽镜像自带的副本，页面读不到数据 |
| `./config` | `/app/config` | **只读** | 因子 JSON + 可选的密钥 txt；宿主必须存在 |
| `./outputs` | `/app/outputs` | 读写 | 审计产物（发票台账、车队审计等，含车牌/发票号，**不要外传**） |
| `./backups` | `/app/backups` | 读写 | 备份目录，首次启动由 Docker 自动创建 |

### 原始数据与产物边界

- `data/raw/`（原始附件，含发票图等敏感内容）**不在镜像里**，只有宿主挂载时容器内才可见；
  它同时被 `.gitignore` 挡住，不会进版本库。
- `outputs/` 下的审计产物含车牌 / 发票号，**不进镜像、不进 git**；
  容器内生成后只留在宿主 `outputs/` 目录，请按内部数据管理制度保存，不要打包外发。
- 镜像自带一份 `data/processed`（32 张结果表，约 11 MB）用于开箱即用；
  运行时宿主 `./data` 挂载会覆盖它，因此**宿主 data/processed 缺失时页面会空**，此时从镜像里拷一份出来即可：

  ```bash
  docker compose run --rm --no-deps -v "$(pwd)/data:/app/data" app \
    sh -c 'cp -n /app/data/processed/* /app/data/processed/ 2>/dev/null; true'
  # 更简单：直接从镜像导出
  docker create --name tmp-carbontide carbontide:latest
  docker cp tmp-carbontide:/app/data/processed/. ./data/processed/
  docker rm tmp-carbontide
  ```

---

## 7. 验证清单

起完服务后按顺序过一遍，全部通过才算交付完成。

### 7.1 容器状态

```bash
docker compose ps
# 期望：app / sensor / caddy 三个服务都是 Up (healthy)
```

### 7.2 传感器网关健康检查

```bash
curl -s http://127.0.0.1:5000/api/health
# 期望：{"status":"success"}

curl -sk https://127.0.0.1:8443/api/health
# 期望：{"status":"success"}（-k 跳过自签证书校验）
```

### 7.3 决策台首页

浏览器打开 `https://<主机IP>:8443`（首次访问先按 §4.2 信任证书）：

- 首页正常渲染（暗色主题、极光底图、品牌标志都在）；
- 顶栏可跳「发票感知 / 硬件监控 / 路线沙盘 / 碳台账」四页；
- 首页 KPI 卡有数值（若全为「—」，多半是宿主 `data/processed` 缺失，见 §6）。

### 7.4 四个验收脚本

脚本需要 Node 18+ 与本机浏览器，在**仓库根目录**执行：

```bash
node tools/verify_pages.js http://localhost:8501 _shots
node tools/verify_nav.js http://localhost:8501
node tools/verify_sliders.js http://localhost:8501 _shots
node tools/verify_dashboard_layout.js http://localhost:8501 _shots
```

- `verify_pages.js`：逐页截图 + 读回关键指标，判据是**截图体积**（空白页约 140 KB，有内容约 1.3–1.9 MB）；
- `verify_nav.js`：点顶栏 / 首页 CTA，验证真的跳到目标页；
- `verify_sliders.js`：真实拖拽自绘滑条，验证值回传 Streamlit；
- `verify_dashboard_layout.js`：查方块重叠与间距。

脚本默认按 Windows 的 Edge / Chrome 路径找浏览器；Linux 上请用环境变量指定：

```bash
CHROME_PATH=/usr/bin/google-chrome node tools/verify_pages.js http://localhost:8501 _shots
```

> Node 20 需要 `--experimental-websocket` 才有全局 `WebSocket`（脚本用内置 WebSocket 直连 CDP，不装 puppeteer）：
> `NODE_OPTIONS=--experimental-websocket CHROME_PATH=/usr/bin/google-chrome node tools/verify_pages.js ...`

### 7.5 ESP32 上报

```bash
# 先用 python -m tools.device_tokens add 登记设备，并保存只打印一次的令牌。
curl -sk -X POST https://<主机IP>:8443/api/upload_sensor \
  -H 'Content-Type: application/json' \
  -H 'X-Device-Token: <设备明文令牌>' \
  -d '{"device_id":"ESP32-01","co2":520.5,"temp":26.3}'
# 期望：{"status":"success"}；缺令牌或设备未登记时返回 401。
```

上报后数据落在**传感器容器内**的 `/app/data/sensor_data.csv`，
该文件位于 `./data:/app/data` 挂载卷，刷新「硬件监控」页可见读数。

---

## 8. 常见问题

### 8.1 端口被占用

```bash
docker compose ps                     # 看是谁占了
ss -lntp | grep -E '8501|5000|8080|8443'
```

改 `deploy/env` 里的 `APP_PORT` / `SENSOR_PORT` / `HTTP_PORT` / `HTTPS_PORT` 后 `up -d` 即可；
容器内监听端口固定，不用改。

### 8.2 页面中文变成方块 / 图表字体缺失

镜像已装 `fonts-noto-cjk`。若仍异常：

```bash
docker compose exec app fc-list | grep -i noto | head
```

为宿主机不需要装字体 —— 字体是**浏览器端**渲染的，客户端机器缺中文字体才会出现方块，
在客户端装 `fonts-noto-cjk` 即可。

### 8.3 只读挂载导致写不进去

`./config` 是只读挂载（`:ro`），程序若要往 `/app/config` 写文件会报 `Permission denied`。
这是**配置目录本该只读**，正确做法是改程序去写 `/app/outputs` 或 `/app/data`，不要把挂载改成可写。

### 8.4 `data/raw` 不在镜像里，容器里找不到原始数据

这是设计如此：原始数据**永不入镜像**。需要跑流水线时把宿主 `data/raw` 挂进去即可
（compose 已经把 `./data` 挂到 `/app/data`），或临时：

```bash
docker compose run --rm -v /path/to/raw:/app/data/raw app python -m src.pipeline
```

### 8.5 页面空 / KPI 全是「—」

宿主 `data/processed` 缺失或为空，挂载把镜像自带的副本遮蔽了。按 §6 把结果表拷回宿主 `data/processed/`。

### 8.6 `Permission denied` 写 `data/` 或 `outputs/`

容器内以非 root 用户 `app`（uid 1000）运行。Linux 宿主上需保证挂载目录对该用户可写：

```bash
sudo chown -R 1000:1000 ./data ./outputs ./backups
```

Windows / macOS 的 Docker Desktop 会自动映射权限，一般不需处理。

### 8.7 Caddy 起不来

```bash
docker compose logs caddy
```

- `caddy_data` 卷里的 CA 损坏 → `docker compose down -v` 后重启（客户端需重新信任证书）；
- 8443 被占 → 改 `HTTPS_PORT`；
- `sensor:5000` / `app:8501` 解析失败 → 两个服务是否 healthy（`docker compose ps`）。

### 8.8 传感器数据重启后没了

`server.py` 默认在容器内写 `/app/data/sensor_data.csv`，由 compose 的
`./data:/app/data` 挂载持久化。若仍报“重启后丢失”，先检查是否手动改过
`SENSOR_DATA_FILE`，以及 `./data` 是否为空目录覆盖了镜像结果。

---

## 9. 升级与回滚

### 9.1 升级

```bash
git pull                                   # 拉新代码
docker compose --env-file deploy/env build  # 重新构建镜像
docker compose --env-file deploy/env up -d  # 滚动重建容器
docker compose ps                          # 确认三个服务都 healthy
```

结果表与审计产物在宿主挂载目录里，升级不影响数据。

### 9.2 回滚

镜像标签带版本，回滚就是把 `IMAGE_NAME` 指回旧镜像：

```bash
# 1. 看本地还有哪些旧镜像
docker images carbontide --format '{{.Repository}}:{{.Tag}} {{.CreatedAt}}'

# 2. 改 deploy/env
#    IMAGE_NAME=carbontide:2027-04-21

# 3. 重建容器（镜像已存在则不再 build）
docker compose --env-file deploy/env up -d
```

若旧镜像已被清理，先回滚代码再 `build`：

```bash
git checkout <上一个稳定提交>
docker compose --env-file deploy/env build && docker compose --env-file deploy/env up -d
```

### 9.3 数据回滚

`data/`、`outputs/`、`config/` 都在宿主目录，不随容器重建丢失；
备份策略建议直接对 `data/processed/` 与 `outputs/` 做宿主机级目录备份（落在 `./backups` 或外部存储）。

---

## 10. 常用命令速查

```bash
docker compose --env-file deploy/env ps
docker compose --env-file deploy/env logs -f app
docker compose --env-file deploy/env restart app
docker compose --env-file deploy/env exec app sh -c 'ls -la /app/data/processed | head'
docker compose --env-file deploy/env exec app cat /app/data/processed/kpi.json
docker compose --env-file deploy/env down
```
