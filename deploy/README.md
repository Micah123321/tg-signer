# Docker Compose 部署

面向**只装 Docker、不装 Python** 的常驻部署：用本目录的 Compose 同时跑 **签到 CLI** 与 **WebUI**，数据落在 `./data`。

镜像构建/本地 Dockerfile 说明见 [../docker/README.md](../docker/README.md)。

## 目录结构

```text
deploy/
├── docker-compose.yml   # 双服务：tg-signer + tg-signer-webui
├── .env.example         # 环境变量模板
├── data/                # 挂载到容器 /opt/tg-signer（session、配置、日志）
└── README.md            # 本文件
```

| 路径（相对 `data/`） | 说明 |
|----------------------|------|
| `<account>.session` | Telegram session（默认 `my_account.session`） |
| `.signer/` | 配置、SQLite 签到记录、自动化状态等 |
| `logs/` | 日志 |

## 前置条件

- Docker Engine + Compose 插件（`docker compose version` 可用）
- 能拉取 `ghcr.io/micah123321/tg-signer`（公共镜像一般无需登录 GHCR）
- 若 Telegram 需代理：宿主机或可达的代理地址

## 快速开始

以下命令均在 **`deploy/` 目录**下执行。

### 1. 准备配置

```sh
cp .env.example .env
# 编辑 .env：代理、时区、授权码、任务名等
```

建议至少修改：

- `TG_SIGNER_GUI_AUTHCODE`：WebUI 授权码
- `TG_PROXY`：按平台填写，或留空
- `TG_SIGN_TASK`：将要常驻的签到任务名（默认 `my_sign`）
- `TG_ACCOUNT`：多账号时改 session 名

### 2. 登录 Telegram（交互，一次性）

Compose 常驻服务是非交互的，**首次请先登录**，把 session 写进 `./data`：

```sh
docker compose run --rm --no-deps \
  -it \
  tg-signer \
  tg-signer login
```

说明：

- 数据写入 `./data`（与 compose 中卷一致）
- 代理/账号读取自 `.env`
- 成功后目录中会出现 `*.session` 与 `.signer/`

等价的 `docker run`（任选）：

```sh
docker run -it --rm \
  --volume "$PWD/data:/opt/tg-signer" \
  --env-file .env \
  ghcr.io/micah123321/tg-signer:latest \
  tg-signer login
```

### 3. 配置签到任务（首次）

若还没有与 `.env` 中 `TG_SIGN_TASK` 同名的任务配置，先交互创建（下面以默认 `my_sign` 为例，请改成你的任务名）：

```sh
docker compose run --rm --no-deps \
  -it \
  tg-signer \
  tg-signer run my_sign
```

按提示保存配置后，用 `Ctrl+C` 结束本次交互即可。配置会落在 `data/.signer/`。

> 注意：本目录 Compose 采用 **CLI `run` 常驻 + WebUI `webgui`**。不要改用镜像的 `serve`（进程内调度）再叠加 CLI `run`，否则会双重定时。

### 4. 启动常驻服务

```sh
docker compose up -d
docker compose ps
docker compose logs -f
```

| 服务 | 容器名 | 作用 |
|------|--------|------|
| `tg-signer` | `tg-signer` | 后台执行 `tg-signer run <TG_SIGN_TASK>` |
| `tg-signer-webui` | `tg-signer-webui` | `tg-signer webgui`，默认 `http://127.0.0.1:8080` |

浏览器打开 `http://127.0.0.1:${WEBUI_PORT}`，使用 `.env` 中的 `TG_SIGNER_GUI_AUTHCODE` 进入。

### 5. 仅启动其中一项（可选）

```sh
# 只要签到
docker compose up -d tg-signer

# 只要 WebUI
docker compose up -d tg-signer-webui
```

## 日常运维

```sh
# 日志
docker compose logs -f tg-signer
docker compose logs -f tg-signer-webui

# 在签到容器内执行 CLI
docker compose exec tg-signer tg-signer list
docker compose exec tg-signer tg-signer run-once my_sign
docker compose exec tg-signer tg-signer automation list

# 更新镜像并重建
docker compose pull
docker compose up -d

# 停止 / 删除容器（保留 data/）
docker compose down
```

一次性命令（不占用常驻容器）：

```sh
docker compose run --rm --no-deps tg-signer \
  tg-signer send-text @neo hello
```

## 环境变量

| 变量 | 用途 |
|------|------|
| `TG_IMAGE_TAG` | 镜像 tag，默认 `latest`；WebUI 使用 `${TG_IMAGE_TAG}-webui` |
| `TZ` | 调度时区 |
| `TG_PROXY` | Telegram 代理 |
| `TG_ACCOUNT` | 账号名 / session 文件名 |
| `TG_SESSION_STRING` | 可选，直接注入 session string |
| `TG_SIGN_TASK` | 常驻签到任务名 |
| `TG_SIGNER_GUI_AUTHCODE` | WebUI 授权码 |
| `WEBUI_PORT` | 宿主机映射端口，默认 `8080` |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` | AI 能力（按需） |

完整 CLI 与任务配置见仓库根目录 [README.md](../README.md)。

## 代理提示

| 环境 | 常见写法 |
|------|----------|
| Linux 宿主机代理 | `socks5://172.17.0.1:7890` |
| Docker Desktop（Windows / macOS） | `socks5://host.docker.internal:7890` |
| 不需要代理 | `TG_PROXY=` 留空 |

## 故障排查

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| 签到容器反复重启 | 任务 `TG_SIGN_TASK` 不存在 / 未 login | 先 `login`，再 `run <任务>` 交互配置；`docker compose logs tg-signer` |
| 拉不下镜像 | 网络 / 镜像名错误 | 检查 `TG_IMAGE_TAG`；手动 `docker pull ghcr.io/micah123321/tg-signer:latest` |
| WebUI 打不开 | 端口占用或未映射 | 改 `WEBUI_PORT`；确认 `docker compose ps` 中 webui 为 Up |
| 授权码错误 | 与 `.env` 不一致 | 修改 `TG_SIGNER_GUI_AUTHCODE` 后 `docker compose up -d tg-signer-webui` |
| 代理连不上 | 地址/协议不对 | 按上表改 `TG_PROXY`，或临时清空验证是否为代理问题 |
| session 被挤下线 | 多容器/多端共用同一账号冲突 | 同一 `data/` 不要叠多个会登录的 CLI 实例；`run` 与 `webgui` 共享 session 时尽量避免并行交互登录 |
| `env file .env not found` | 未复制环境变量模板 | `cp .env.example .env` 后再 `docker compose up` |

## 安全建议

- 不要将含真实授权码的 `.env`、`data/*.session`、`data/.signer/` 提交到 Git
- 公网暴露 WebUI 前务必设置强 `TG_SIGNER_GUI_AUTHCODE`，并考虑反代与 HTTPS
- 生产环境可将 `TG_IMAGE_TAG` 钉在具体版本（如 `v0.9.0`），避免 `latest` 意外升级

## 与 `docker/` 目录的关系

| 目录 | 职责 |
|------|------|
| **`deploy/`（本目录）** | 推荐：用预构建镜像 Compose 常驻部署 CLI + WebUI |
| **`docker/`** | 镜像 Dockerfile、本地构建、以及基于本地 build 的参考 compose |
