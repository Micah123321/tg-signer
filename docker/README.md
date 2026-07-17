# Docker 使用说明

面向**主机不安装 Python** 的用法：直接使用 GHCR 预构建镜像，把数据目录挂载到容器即可。

根 README 中的 Docker 章节是精简版；本文件补充本地构建、Compose 与目录约定。

## 镜像一览

| 镜像 | 内容 | 默认启动 |
|------|------|----------|
| `ghcr.io/micah123321/tg-signer:<tag>` / `:latest` | CLI + `tgcrypto` | 无固定 CMD，需传入 `tg-signer ...` |
| `ghcr.io/micah123321/tg-signer:<tag>-webui` / `:latest-webui` | CLI + WebUI（nicegui） | `tg-signer serve --host 0.0.0.0 --port 8080` |

### 自动构建策略

| 触发 | 推送的镜像 tag（cli / webui） |
|------|------------------------------|
| `main` 分支 push | `:latest` / `:latest-webui`、`:main` / `:main-webui`、`:<short-sha>` / `:<short-sha>-webui` |
| 推送合法 Git tag | `:<tag>` / `:<tag>-webui`，并同步更新 `latest` / `latest-webui` |
| 手动 `workflow_dispatch` | 仅 `:<image_tag>` / `:<image_tag>-webui`（不覆盖 `latest`） |

合法 Docker tag 示例：`v0.9.0`、`0.9.0`、`v0.9.0b2`、7 位 commit short sha。

### 手动测试推送

不想因推送 Git tag 触发 PyPI 发布时，可在 GitHub Actions 手动运行 `Publish Docker Image`：

- `ref`：要构建的分支、提交或 tag
- `image_tag`：推到 GHCR 的测试 tag，例如 `manual-test`、`pr-123`

手动触发只推送指定 tag 与对应 `-webui`，不会覆盖 `latest` / `latest-webui`。

## 推荐工作流（预构建镜像）

### 1. 数据目录

```sh
mkdir -p tg-signer-data
cd tg-signer-data
```

容器工作目录为 `/opt/tg-signer`，请始终把数据目录挂到这里。落盘内容：

| 路径（相对挂载点） | 说明 |
|--------------------|------|
| `<account>.session` | Telegram session（默认 `my_account.session`） |
| `.signer/` | 配置、SQLite 签到记录、自动化状态等 |
| `logs/` | 日志 |

### 2. 登录

```sh
docker run -it --rm \
  --volume "$PWD:/opt/tg-signer" \
  --env TG_PROXY=socks5://172.17.0.1:7890 \
  --env TZ=Asia/Shanghai \
  ghcr.io/micah123321/tg-signer:latest \
  tg-signer login
```

代理提示：

- Linux 访问宿主机代理：常见为 `172.17.0.1`
- Docker Desktop（Windows / macOS）：可用 `host.docker.internal`
- 不需要代理：去掉 `TG_PROXY`

### 3. 配置并后台运行签到

```sh
# 交互配置
docker run -it --rm \
  --volume "$PWD:/opt/tg-signer" \
  --env TG_PROXY=socks5://172.17.0.1:7890 \
  --env TZ=Asia/Shanghai \
  ghcr.io/micah123321/tg-signer:latest \
  tg-signer run my_sign

# 后台常驻
docker run -d --name tg-signer \
  --restart unless-stopped \
  --volume "$PWD:/opt/tg-signer" \
  --env TG_PROXY=socks5://172.17.0.1:7890 \
  --env TZ=Asia/Shanghai \
  ghcr.io/micah123321/tg-signer:latest \
  tg-signer run my_sign
```

运维：

```sh
docker logs -f tg-signer
docker exec -it tg-signer tg-signer list
docker exec -it tg-signer tg-signer run-once my_sign
docker exec -it tg-signer tg-signer automation init my_auto
docker exec -it tg-signer tg-signer automation run my_auto
```

### 4. WebUI

```sh
docker run -d --name tg-signer-webui \
  --restart unless-stopped \
  --volume "$PWD:/opt/tg-signer" \
  --publish 8080:8080 \
  --env TG_PROXY=socks5://172.17.0.1:7890 \
  --env TZ=Asia/Shanghai \
  --env TG_SIGNER_GUI_AUTHCODE=change-me \
  ghcr.io/micah123321/tg-signer:latest-webui
```

访问 `http://127.0.0.1:8080`，使用 `TG_SIGNER_GUI_AUTHCODE` 授权码进入。

### 5. 环境变量

| 变量 | 用途 |
|------|------|
| `TG_PROXY` | Telegram 代理 |
| `TG_ACCOUNT` | 账号名 / session 文件名（默认 `my_account`） |
| `TG_SESSION_STRING` | 直接注入 session string（可选） |
| `TZ` | 调度时区（运行时优先；未设置则本地时区 → `Asia/Shanghai`） |
| `TG_SIGNER_GUI_AUTHCODE` | WebUI 授权码 |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` | AI 能力（识图、计算题等） |

### 6. 一次性命令

```sh
docker run --rm \
  --volume "$PWD:/opt/tg-signer" \
  --env TZ=Asia/Shanghai \
  ghcr.io/micah123321/tg-signer:latest \
  tg-signer send-text @neo hello
```

## 本地构建

仓库内 Dockerfile：

| 文件 | 用途 |
|------|------|
| `GHCR.Dockerfile` | 官方多阶段发布（`cli` / `webui` target） |
| `Dockerfile` | 通用本地构建（从 PyPI 安装） |
| `CN.Dockerfile` | 国内镜像源（清华 apt + PyPI） |

在 `docker/` 目录下执行：

```sh
# 通用
docker build -t tg-signer:latest -f Dockerfile .

# 国内源
docker build -t tg-signer:latest -f CN.Dockerfile .

# 指定系统时区写入镜像
docker build --build-arg TZ=Europe/Paris -t tg-signer:latest -f CN.Dockerfile .
```

本地镜像运行（与预构建相同，只是镜像名不同）：

```sh
docker run -d --name tg-signer \
  --restart unless-stopped \
  --volume "$PWD:/opt/tg-signer" \
  --env TG_PROXY=socks5://172.17.0.1:7890 \
  --env TZ=Asia/Shanghai \
  tg-signer:latest \
  tg-signer run my_sign
```

## Docker Compose

### 推荐：预构建镜像常驻部署（CLI + WebUI）

长期运行签到 + WebUI 请使用仓库根目录下的 **[deploy/](../deploy/)**（`ghcr.io` 预构建镜像、`.env`、共享 `data/`）。本目录的 `docker-compose.yml` 面向**本地构建**调试，不是首选生产路径。详见 [deploy/README.md](../deploy/README.md)。

### 本地构建 Compose（本目录）

`docker-compose.yml` 默认基于 `CN.Dockerfile` 本地构建，并把**当前目录**挂到 `/opt/tg-signer`。

可选：在数据目录放 `start.sh` 作为启动入口（方便改启动命令而不改 compose）：

```sh
#!/bin/bash
set -e
# 首次配置可先 sleep infinity，exec 进容器配置后再改回业务命令
tg-signer run my_sign
```

```sh
chmod +x start.sh
# 在 docker/ 目录或按 compose 中的 context 调整后：
docker compose up -d
# 或指定时区
TZ=Europe/Paris docker compose up -d
```

进入容器：

```sh
docker exec -it tg-signer bash
# 或直接执行子命令
docker exec -it tg-signer tg-signer login
```

若希望 Compose 直接使用预构建镜像而不是本地 build，可将 service 改为：

```yaml
services:
  tg-signer:
    image: ghcr.io/micah123321/tg-signer:latest
    container_name: tg-signer
    command: ["tg-signer", "run", "my_sign"]
    volumes:
      - $PWD:/opt/tg-signer
    environment:
      - TG_PROXY=socks5://172.17.0.1:7890
      - TZ=${TZ:-Asia/Shanghai}
    restart: unless-stopped
```

WebUI Compose 示例：

```yaml
services:
  tg-signer-webui:
    image: ghcr.io/micah123321/tg-signer:latest-webui
    container_name: tg-signer-webui
    volumes:
      - $PWD:/opt/tg-signer
    ports:
      - "8080:8080"
    environment:
      - TG_PROXY=socks5://172.17.0.1:7890
      - TZ=${TZ:-Asia/Shanghai}
      - TG_SIGNER_GUI_AUTHCODE=change-me
    restart: unless-stopped
```

## 时区

调度命令解析顺序：`TZ` 环境变量 → 容器本地时区 → `Asia/Shanghai`。

- 运行时：`--env TZ=Europe/Paris` 或 compose 中的 `TZ=...`
- 本地构建时：`--build-arg TZ=Europe/Paris` 可同步写入系统时区文件

## 配置任务

登录与任务配置细节（动作流、automation、monitor 等）见仓库根目录 [README.md](../README.md)。容器内命令与主机安装后完全相同，只需通过 `docker run` / `docker exec` 调用。
