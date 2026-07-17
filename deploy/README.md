# Docker Compose 部署

面向**只装 Docker、不装 Python** 的常驻部署。本目录默认跑 **Web 运维台 `serve`**（页面上管理多账号、计划表、执行历史），数据落在 `./data`。

镜像可用两种来源（在 `.env` 里选）：

| 模式 | `.env` 要点 | 日常更新 |
|------|-------------|----------|
| **预构建（默认）** | `TG_IMAGE_REGISTRY=ghcr.io/micah123321/tg-signer`、`TG_IMAGE_TAG=latest`、`TG_PULL_POLICY=missing` | `docker compose pull && docker compose up -d` |
| **本地编译** | `TG_IMAGE_REGISTRY=tg-signer`、`TG_IMAGE_TAG=local`、`TG_PULL_POLICY=build` | `git pull` 后 `docker compose up -d --build --force-recreate` |

Dockerfile 见 [../docker/GHCR.Dockerfile](../docker/GHCR.Dockerfile)（`cli` / `webui` target）。

## 一键开始（预构建）

```sh
git clone https://github.com/Micah123321/tg-signer.git
cd tg-signer/deploy
cp .env.example .env
# 编辑 .env：至少改 TG_SIGNER_GUI_AUTHCODE；按需设 TG_PROXY、TZ、TG_IMAGE_TAG
docker compose up -d
```

浏览器打开 `http://127.0.0.1:8080`（端口见 `.env` 的 `WEBUI_PORT`），输入授权码。

首次使用还需：**登录账号** + **配置签到任务** + **在计划表里分配时刻**（见下方完整步骤）。仅 `up -d` 不会自动完成 Telegram 登录。

### 已有仓库 · 预构建更新

```sh
cd tg-signer/deploy   # 或你的 fork 路径
git pull              # 可选：只更新部署文件/文档
cp -n .env.example .env
docker compose pull
docker compose up -d
```

### 本地源码编译

适合改代码、跟 `main`、或暂时拉不到 GHCR 时。须在**完整 git 仓库**中操作（build context 为仓库根目录）。

1. 编辑 `deploy/.env`（可从 `.env.example` 复制后改这三项）：

```env
TG_IMAGE_REGISTRY=tg-signer
TG_IMAGE_TAG=local
TG_PULL_POLICY=build
```

2. 首次或代码更新后：

```sh
cd tg-signer            # 仓库根
git pull
cd deploy
cp -n .env.example .env # 已有 .env 则跳过；确认上面三项为 local/build
docker compose up -d --build --force-recreate
```

等价镜像名：`tg-signer:local-webui`（WebUI）、`tg-signer:local`（legacy-run CLI）。

`TG_PULL_POLICY=build` 时 Compose 会走本地 `build:`（`../docker/GHCR.Dockerfile`），不依赖 GHCR 上的 tag。仍建议保留 `--build`，确保 `git pull` 后层缓存按 Dockerfile 更新。

## 目录结构

```text
deploy/
├── docker-compose.yml   # 默认：tg-signer-webui (serve)；可选 profile：legacy-run
├── .env.example         # 环境变量模板（含预构建 / 本地编译切换说明）
├── data/                # 挂载到容器 /opt/tg-signer
└── README.md            # 本文件
```

| 路径（相对 `data/`） | 说明 |
|----------------------|------|
| `<account>.session` | Telegram session（如 `a0.session`） |
| `.signer/` | 任务配置、SQLite（签到记录 + 计划/执行历史）等 |
| `logs/` | 日志 |

## 前置条件

- Docker Engine + Compose 插件（`docker compose version` 可用；`pull_policy` 需 Compose V2.22+，过旧时用 `up --build` 即可）
- **预构建**：能拉取 `ghcr.io/micah123321/tg-signer`（建议 `TG_IMAGE_TAG=main` 或含 `serve` 的新版本）
- **本地编译**：完整克隆本仓库（含 `docker/GHCR.Dockerfile`），构建时不强制访问 GHCR
- 若 Telegram 需代理：宿主机或可达的代理地址

## 推荐模式：Web 计划调度（替代 bash 串跑）

过去常见做法是主机上写脚本循环：

```sh
docker exec -e TG_PROXY=... tg-signer-webui \
  tg-signer -a a0 --in-memory run-once sg-sign
```

现在改为：**一个 `serve` 容器** + 浏览器里配「账号 × 任务 × 每日时刻」。

### 1. 准备配置

若尚未执行「一键开始」：

```sh
git clone https://github.com/Micah123321/tg-signer.git
cd tg-signer/deploy
cp .env.example .env
# 编辑 .env：至少改 TG_SIGNER_GUI_AUTHCODE；按需设 TG_PROXY、TZ
```

已在 `deploy/` 目录时只需：

```sh
cp .env.example .env
# 编辑 .env
```

### 2. 多账号登录（每个账号一次）

Compose 常驻服务非交互，**先登录**再 `up`。session 写到 `./data`：

```sh
# 账号 a0（生成 data/a0.session）
docker compose run --rm --no-deps -it \
  tg-signer-webui \
  tg-signer -a a0 login

# 账号 a1、a2 …
docker compose run --rm --no-deps -it \
  tg-signer-webui \
  tg-signer -a a1 login
```

代理/时区读自 `.env`。成功后 `data/` 下应有 `*.session` 与 `.signer/`。

### 3. 创建签到任务配置（与账号无关，可多账号共用）

任务配置在 `data/.signer/signs/<任务名>/config.json`。首次可用交互：

```sh
# 例如创建任务 sg-sign、lam-sign（名称自定）
docker compose run --rm --no-deps -it \
  tg-signer-webui \
  tg-signer -a a0 run sg-sign
```

按提示写好 chats/动作后 `Ctrl+C` 退出即可。也可用 Web 打开后的「配置管理 → Signer」编辑。

同一任务名可被多个账号在计划表中引用（等价于以前的 multi-run / 脚本多次 `-a`）。

### 4. 启动运维台

```sh
docker compose up -d
docker compose ps
docker compose logs -f tg-signer-webui
```

| 服务 | 容器名 | 默认行为 |
|------|--------|----------|
| `tg-signer-webui` | `tg-signer-webui` | `tg-signer serve`：WebUI + **进程内计划调度** |

浏览器：`http://127.0.0.1:${WEBUI_PORT}`，输入 `.env` 中的 `TG_SIGNER_GUI_AUTHCODE`。

### 5. 在网页完成「账号 → 计划」

1. **账号**  
   - 列表会扫描 `data/*.session`  
   - 可为每个账号填写 **代理**（覆盖全局 `TG_PROXY`），点保存  

2. **配置管理 → Signer**  
   - 确认已有 `sg-sign` / `lam-sign` 等任务（或在此新建）  

3. **计划表**（首页）  
   新建多条计划，对应以前的 bash 任务列表，例如：  

   | 账号 | 类型 | 任务名 | 时刻/cron | 说明 |
   |------|------|--------|-----------|------|
   | a0 | sign | sg-sign | `06:00:00` 或 `0 6 * * *` | 每天 6 点 |
   | a1 | sign | sg-sign | `06:05:00` | 错开时间 |
   | a2 | sign | sg-sign | `06:10:00` | |
   | a0 | sign | lam-sign | `07:00:00` | 同账号多任务串行 |
   | a0 | sign | hy-sign | `07:30:00` | |

   - 同账号多计划会 **串行**；不同账号可 **并行**  
   - 支持「立即执行」、启停、JSON 导入/导出  
   - **执行历史** 查看成功/失败/重试  

4. **不要再**对同一账号任务：  
   - 主机 bash 循环 `run-once`  
   - 或 `docker compose --profile legacy-run` 的 CLI `run` 常驻  
   否则会与计划调度 **双重定时**。

### 6. 与旧 bash 脚本的对应关系

| 旧脚本 | 新做法 |
|--------|--------|
| `PROXY=... docker exec ... -e TG_PROXY` | `.env` 的 `TG_PROXY`，或 Web「账号」per-account 代理 |
| `-a a0 run-once sg-sign` | 计划：account=a0, task=sg-sign, 时刻=… |
| `sleep 60` 防冲突 | 调度器同账号互斥队列 + 计划错开时间 |
| 多行 tasks 数组 | 计划表多行 CRUD / 导入 JSON |

导出计划示例（Web 导出或 SQLite 中结构概念）：

```json
{
  "version": 1,
  "plans": [
    {
      "account": "a0",
      "task_type": "sign",
      "task_ref": "sg-sign",
      "schedule_expr": "06:00:00",
      "random_seconds": 0,
      "enabled": true,
      "max_retries": 1
    }
  ]
}
```

## 日常运维

```sh
# 日志
docker compose logs -f tg-signer-webui

# 容器内 CLI（login、list、临时 run-once 调试）
docker compose exec tg-signer-webui tg-signer list
docker compose exec tg-signer-webui tg-signer -a a0 run-once sg-sign

# 更新 · 预构建（TG_PULL_POLICY=missing|always）
docker compose pull
docker compose up -d

# 更新 · 本地编译（TG_PULL_POLICY=build，先 git pull 仓库）
docker compose up -d --build --force-recreate

# 停止（保留 data/）
docker compose down
```

一次性命令（不占用常驻容器）：

```sh
docker compose run --rm --no-deps tg-signer-webui \
  tg-signer -a a0 send-text @neo hello
```

## 可选：旧版单任务 CLI 常驻

仅当你**不用**计划表、只要「一个账号 + 一个任务」死循环 `run` 时：

```sh
# 在 .env 设置 TG_ACCOUNT、TG_SIGN_TASK
docker compose --profile legacy-run up -d
```

会额外启动 `tg-signer` 容器执行 `tg-signer run $TG_SIGN_TASK`。

> **禁止**在已用 `serve` 计划调度的同一账号/任务上再开 `legacy-run`。

若只要配置编辑、不要进程内调度，可临时改 compose 中 command 为：

```yaml
command: ["tg-signer", "webgui", "--host", "0.0.0.0", "--port", "8080"]
```

## 环境变量

| 变量 | 用途 |
|------|------|
| `TG_IMAGE_REGISTRY` | 镜像名（不含 tag）。预构建：`ghcr.io/micah123321/tg-signer`；本地：`tg-signer` |
| `TG_IMAGE_TAG` | tag 前缀。WebUI 实际为 `${TG_IMAGE_TAG}-webui`，CLI 为 `${TG_IMAGE_TAG}` |
| `TG_PULL_POLICY` | Compose `pull_policy`：`missing`/`always` 走拉取；`build` 走本地编译 |
| `TZ` | 调度时区（同时传入镜像 build-arg） |
| `TG_PROXY` | 默认 Telegram 代理 |
| `TG_ACCOUNT` | 默认账号名（CLI 未传 `-a` 时） |
| `TG_SESSION_STRING` | 可选 session string |
| `TG_SIGNER_GUI_AUTHCODE` | WebUI 授权码 |
| `WEBUI_PORT` | 宿主机端口，默认 `8080` |
| `TG_NUM_OF_DIALOGS` | serve 签到拉取最近对话数 |
| `TG_SIGN_TASK` | 仅 `legacy-run` profile |
| `OPENAI_*` | AI 能力（按需） |

## 代理提示

| 环境 | 常见写法 |
|------|----------|
| Linux 宿主机代理 | `socks5://172.17.0.1:7890` |
| Docker Desktop（Windows / macOS） | `socks5://host.docker.internal:7890` |
| 不需要代理 | `TG_PROXY=` 留空 |

## 故障排查

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| `serve` / 未知命令 | 镜像过旧 | 预构建：`TG_IMAGE_TAG=main` 后 `pull`；或改本地 `TG_PULL_POLICY=build` 再 `--build` |
| 本地 build 找不到 Dockerfile | 不在 monorepo / context 不对 | 在含 `docker/GHCR.Dockerfile` 的仓库内，于 `deploy/` 执行 compose |
| `pull_policy` 不被识别 | Compose 过旧 | 升级 Docker Compose V2；或显式 `up --build` |
| 计划不执行 | 未启用计划 / 无 session / 调度器未启动 | 确认用的是 `serve`；账号有 session；计划「启用」 |
| 同账号冲突/限流 | 计划挤在同一时刻 | 错开时刻；同账号已串行，仍建议间隔 |
| WebUI 打不开 | 端口占用 | 改 `WEBUI_PORT` |
| 授权码错误 | 与 `.env` 不一致 | 改 `TG_SIGNER_GUI_AUTHCODE` 后 `up -d` |
| `env file .env not found` | 未复制模板 | `cp .env.example .env` |
| session 被挤 | 多实例抢同一 session | 同一 `data/` 不要叠多个会登录的进程 |

## 安全建议

- 不要提交含真实授权码的 `.env`、`data/*.session`、`data/.signer/`
- 公网暴露 WebUI 前设置强授权码，并考虑反代与 HTTPS
- 生产可将 `TG_IMAGE_TAG` 钉在具体版本

## 与 `docker/` 目录的关系

| 目录 | 职责 |
|------|------|
| **`deploy/`（本目录）** | 推荐：Compose 常驻（默认 serve）；`.env` 可在 GHCR 预构建与本地 `GHCR.Dockerfile` 编译间切换 |
| **`docker/`** | Dockerfile、发布用构建说明、历史/本地参考 compose |
