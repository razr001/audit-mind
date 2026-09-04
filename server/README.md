# AuditMind Server 启动与部署说明

本文以当前 `server/` 代码和 Compose 配置为准，说明从空环境启动本地开发服务，以及在生产环境部署 API、Worker 和基础设施的步骤。

## 1. 运行组成

AuditMind Server 不是只启动 FastAPI 就能完整工作。一个可用环境至少包含：

| 组件 | 是否必需 | 作用 |
|---|---|---|
| FastAPI | 必需 | 登录、查询、上传、SSE 和任务投递 |
| Dramatiq Worker | 必需 | 执行法规处理和文档审计长任务 |
| PostgreSQL | 必需 | 唯一业务事实库 |
| Redis | 必需 | Dramatiq 队列、Refresh Token、分布式锁和任务协调 |
| MinIO | 必需 | 保存原文件、MinerU 结果和图片 |
| Elasticsearch | 必需 | 法规 Chunk 和原子规则检索副本 |
| MinerU | PDF 流程必需 | PDF 版面、文字、表格、公式、图片和坐标解析 |
| 文本模型 | 必需 | 规则抽取、法规问答和文档审计 |
| Embedding 模型 | 必需 | 法规索引和审计规则召回 |
| Reranker | 可选 | 对检索候选进行精排；未配置时自动跳过 |
| 视觉模型 | 可选 | MinerU 没有提供图片描述时补充分析 |
| Kibana | 可选 | 查看 Elasticsearch 数据 |
| Alloy、Loki、Grafana | 推荐 | 日志采集、存储和查询 |
| XXL-JOB | 生产推荐 | 定时调用超时任务维护接口；不在本仓库 Compose 中 |

`docker-compose.yml` 只提供基础设施和 MinerU API，不包含 FastAPI、Dramatiq Worker、前端和 Nginx。API 与 Worker 必须单独启动。

## 2. 首次启动必做

完成 `.env` 配置、基础设施启动和数据库迁移后，还必须初始化登录账号并启动 Worker。

### 2.1 初始化首个登录账号

先通过 Alembic 初始化数据库，再创建首个登录账号。以下使用 `auditmind` 作为示例用户名，可以替换为实际需要的名称：

```powershell
uv run alembic upgrade head
uv run python -m scripts.manage_users create auditmind
```

首次迁移 `0001_initial_schema` 会读取 `database/init.sql` 创建完整表结构。不要直接通过 `psql` 导入该文件；`alembic_version` 由 Alembic 自己管理。以后升级仍执行同一条 `uv run alembic upgrade head` 命令。

脚本会在终端中提示输入两次密码，密码不会显示在屏幕上。成功时会输出创建的用户名和用户 ID。

当前用户模型只有用户名和密码，不区分管理员、普通用户等角色。

常用账号维护命令：

```powershell
# 查看所有账号
uv run python -m scripts.manage_users list

# 重置 auditmind 密码
uv run python -m scripts.manage_users set-password auditmind

# 删除指定账号
uv run python -m scripts.manage_users delete username
```

这些命令必须在 `server/` 目录执行，并且 `.env` 中的 `DATABASE_URL` 必须可连接。修改密码或删除用户时，还必须保证 `REDIS_URL` 可连接，服务会同时使该用户已有的 Refresh Token 失效。

### 2.2 启动 Dramatiq Worker

开发环境另开一个终端，在 `server/` 目录执行：

```powershell
uv run dramatiq app.worker --processes 1 --threads 4
```

日志出现下面内容才表示 Worker 已成功启动：

```text
Worker process is ready for action.
```

Worker 必须和 FastAPI 读取同一份 `.env`，尤其要确认 `REDIS_URL` 指向实际 Redis，而不是误连 `localhost`。Worker 未启动时，上传和创建任务的 API 仍可能返回成功，但法规处理和审计任务不会继续执行。

生产环境不要依赖人工终端，必须通过 systemd、Supervisor、Kubernetes Deployment 等进程管理方式持续运行 Worker。本文的生产 systemd 示例见“5.3 systemd 示例”。

## 3. 本地启动

### 3.1 环境要求

- Python 3.12 或更高版本
- [uv](https://docs.astral.sh/uv/)
- Docker 和 Docker Compose
- Node.js/npm（需要运行 `../web` 前端时）
- GPU 模式还需要 Linux、NVIDIA 驱动和 NVIDIA Container Toolkit

以下命令默认在 `server/` 目录执行。

### 3.2 创建配置文件

PowerShell：

```powershell
Copy-Item .env.example .env
Copy-Item docker/elasticsearch/.env.example docker/elasticsearch/.env
Copy-Item docker/kibana/.env.example docker/kibana/.env
Copy-Item docker/grafana/.env.example docker/grafana/.env
```

Linux/macOS：

```bash
cp .env.example .env
cp docker/elasticsearch/.env.example docker/elasticsearch/.env
cp docker/kibana/.env.example docker/kibana/.env
cp docker/grafana/.env.example docker/grafana/.env
```

随后修改所有占位值。真实 `.env` 和 `docker/*/.env` 都不能提交到 Git。

使用当前 Compose 的开发默认账号时，业务 `.env` 至少要对应为：

```dotenv
ENVIRONMENT=local
DATABASE_URL=postgresql+asyncpg://hyperweave:hyperweave123@localhost:5432/hyperweave
REDIS_URL=redis://default:redis123@127.0.0.1:6379/0
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin123
MINIO_BUCKET=auditmind-documents
MINIO_SECURE=false
ELASTICSEARCH_URL=http://localhost:9200
```

这些只是仓库当前的本地开发默认值，不能用于生产。

生成 JWT 或内部调度 Token 时应使用随机值，例如：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 3.3 安装 Python 依赖

```powershell
uv sync
```

### 3.4 初始化 Elasticsearch 和 Kibana

初始化脚本会执行以下操作：

1. 校验 Docker Compose 配置；
2. 启动 Elasticsearch；
3. 交互设置 `elastic` 和 `kibana_system` 密码；
4. 创建 FastAPI 使用的最小权限 API Key，并写入 `.env`；
5. 启动 Kibana。

```powershell
uv run python scripts/bootstrap_infrastructure.py
```

初始化完成后可访问：

- Elasticsearch：`http://localhost:9200`
- Kibana：`http://localhost:5601`

FastAPI 使用 `ELASTICSEARCH_API_KEY`，不要把 `elastic` 管理员密码或 `docker/kibana/.env` 中的 `ELASTICSEARCH_PASSWORD` 写入业务 `.env`。

### 3.5 启动数据库、缓存、对象存储和日志系统

```powershell
docker compose up -d postgresql redis minio loki alloy grafana
docker compose ps
```

本地入口：

- MinIO API：`http://localhost:9000`
- MinIO Console：`http://localhost:9001`
- Grafana：`http://localhost:3101`
- Alloy：`http://localhost:12345`

### 3.6 选择一种 MinerU 启动方式

#### 方式 A：无 GPU 的 CPU 模式

不要同时启动基础 Compose 中的 `mineru-api`，两者默认都会占用宿主机 `8000` 端口。

```powershell
docker compose -f docker-compose.cpu.yml up -d --build
```

`.env` 设置：

```dotenv
MINERU_BASE_URL=http://127.0.0.1:8000
MINERU_BACKEND=pipeline
MINERU_SERVER_URL=
```

CPU 模式适合开发验证，解析大 PDF 会明显较慢。

#### 方式 C：MinerU 云端精准解析 API

无需启动 MinerU Docker 服务，在 `.env` 中配置：

```dotenv
MINERU_PROVIDER=cloud
MINERU_CLOUD_API_TOKEN=在 https://mineru.net/apiManage 创建的 Token
MINERU_CLOUD_MODEL_VERSION=vlm
MINERU_CLOUD_LANGUAGE=ch
```

云端模式使用预签名 URL 流式上传 MinIO 中的原文件，并轮询精准解析 API；
`MINERU_BASE_URL`、`MINERU_BACKEND` 和 `MINERU_SERVER_URL` 仅用于本地模式。

#### 方式 B：NVIDIA GPU / 混合模式

在 GPU 主机启动推理服务：

```powershell
docker compose -f docker-compose.nvidia.yml up -d --build
```

在应用主机启动 MinerU API：

```powershell
docker compose up -d --build mineru-api
```

同机开发设置：

```dotenv
MINERU_BASE_URL=http://127.0.0.1:8000
MINERU_BACKEND=hybrid-http-client
MINERU_SERVER_URL=http://host.docker.internal:30000
```

GPU 服务在另一台物理机时，将 `MINERU_SERVER_URL` 改成 GPU 机器内网地址，例如 `http://192.168.10.20:30000`，并通过 `MINERU_BIND_IP` 只绑定其内网网卡，不能暴露公网。

### 3.7 配置 AI 服务

必须配置文本模型和 Embedding 模型：

```dotenv
AI_BASE_URL=https://your-openai-compatible-endpoint/v1
AI_API_KEY=your-api-key
AI_MODEL=your-chat-model

AI_EMBEDDING_BASE_URL=https://your-embedding-endpoint/v1
AI_EMBEDDING_API_KEY=your-embedding-api-key
AI_EMBEDDING_MODEL=your-embedding-model
AI_EMBEDDING_DIMENSIONS=1024
```

`AI_EMBEDDING_DIMENSIONS` 必须与模型真实输出维度完全一致。配置错误会导致 Elasticsearch Mapping 或向量写入失败。应用允许在 Embedding 留空时启动，但法规索引和审计功能会失败，因此完整部署必须配置。

护栏和查询改写模型未配置时回退到主模型。Reranker 未配置时跳过精排。视觉模型三项任一为空时关闭视觉补充，不影响 MinerU 主流程。

### 3.8 数据库迁移和初始账号

开发环境和已有数据库使用 Alembic：

```powershell
uv run alembic upgrade head
uv run python -m scripts.manage_users create auditmind
```

首次迁移会自动执行 [`database/init.sql`](database/init.sql)，不需要手工导入 SQL。

创建用户时会在终端中要求输入和确认密码。其他维护命令：

```powershell
uv run python -m scripts.manage_users list
uv run python -m scripts.manage_users set-password auditmind
uv run python -m scripts.manage_users delete username
```

### 3.9 启动 FastAPI 和 Dramatiq Worker

终端一：

```powershell
uv run auditmind-api --host 127.0.0.1 --port 8181 --reload
```

请通过项目提供的 `auditmind-api` 入口启动 API。该入口会在 Windows 上使用
`SelectorEventLoop`，避免 Agent PostgreSQL checkpointer 与默认事件循环不兼容；
直接执行 `uvicorn app.main:app` 会绕过这项兼容处理。

终端二：

```powershell
uv run dramatiq app.worker --processes 1 --threads 4
```

看到 `Worker process is ready for action` 才表示 Worker 已就绪。没有 Worker 时，上传接口仍可能成功，但法规处理和审计任务会一直排队。

验证后端：

```powershell
curl.exe http://127.0.0.1:8181/health
```

本地接口文档：`http://127.0.0.1:8181/docs`。生产环境不会开放接口文档。

### 3.10 启动前端

另开终端进入仓库的 `web/`：

```powershell
npm ci
npm run dev
```

访问 `http://localhost:5173`。Vite 会把 `/api` 转发到 `http://localhost:8181`，并通过本地 `/minio` 代理访问预签名文件地址。

## 4. 必需配置说明

以 `.env.example` 为完整模板，以下配置是上线前必须确认的核心项：

| 配置 | 关键变量 | 注意事项 |
|---|---|---|
| 环境 | `ENVIRONMENT` | 本地使用 `local`；生产使用 `production` |
| 数据库 | `DATABASE_URL` | 必须是 `postgresql+asyncpg://` 连接串 |
| Redis | `REDIS_URL` | API 与 Worker 必须连接同一个 Redis；生产主机不能误写 `localhost` |
| MinIO | `MINIO_ENDPOINT`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`、`MINIO_BUCKET` | Endpoint 不带 `http://`；Bucket 只在应用启动时创建 |
| Elasticsearch | `ELASTICSEARCH_URL`、`ELASTICSEARCH_API_KEY` | 业务使用最小权限 API Key，不使用管理员账号 |
| 索引 | `ELASTICSEARCH_REGULATION_CHUNK_INDEX`、`ELASTICSEARCH_REGULATION_RULE_INDEX` | 使用显式版本名；API 与 Worker 配置必须一致 |
| JWT | `JWT_SECRET_KEY`、`JWT_ISSUER`、`JWT_AUDIENCE` | Secret 至少 32 字符且必须随机 |
| Refresh Cookie | `JWT_REFRESH_EXPIRATION_DAYS`、`AUTH_COOKIE_SECURE`、`AUTH_COOKIE_SAMESITE` | HTTPS 生产必须设置 `AUTH_COOKIE_SECURE=true` |
| 主模型 | `AI_BASE_URL`、`AI_API_KEY`、`AI_MODEL` | 要求 OpenAI 兼容接口 |
| Embedding | `AI_EMBEDDING_*` | 完整业务必需，维度必须匹配 |
| MinerU | `MINERU_PROVIDER`、`MINERU_BASE_URL`、`MINERU_CLOUD_API_TOKEN` | 本地 CPU/GPU 与云端模式配置不同 |
| 请求上限 | `REQUEST_BODY_MAX_BYTES`、`DOCUMENT_MAX_FILE_SIZE`、`REGULATION_MAX_FILE_SIZE` | 普通请求默认 10 MiB，PDF 默认 100 MiB |
| 长任务 | `DRAMATIQ_*_TIME_LIMIT_SECONDS`、`*_PIPELINE_WAIT_TIMEOUT_SECONDS` | Dramatiq 时限至少比对应等待时限多 300 秒 |
| 日志 | `LOG_FILE_PATH` | 本地默认写 JSONL；容器采集 stdout 时可留空，避免重复日志 |
| 维护任务 | `SCHEDULER_ACCESS_TOKEN` | 留空会禁用内部维护接口；启用时至少 32 字符 |

本地跨域使用：

```dotenv
CORS_ALLOWED_ORIGINS=["http://localhost:5173"]
AUTH_COOKIE_SECURE=false
```

生产前后端同源部署通常使用：

```dotenv
ENVIRONMENT=production
CORS_ALLOWED_ORIGINS=[]
AUTH_COOKIE_SECURE=true
```

## 5. 生产部署

### 5.1 推荐拓扑

- Nginx 是唯一面向用户的入口，提供前端静态文件、`/api` 和 `/minio` 代理。
- FastAPI 与 Dramatiq Worker 独立进程部署，可在不同机器运行。
- API 和 Worker 必须访问同一套 PostgreSQL、Redis、MinIO、Elasticsearch、MinerU 和模型服务。
- PostgreSQL、Redis、MinIO、Elasticsearch 必须使用持久卷或受管服务。
- MinerU GPU Server、Redis、PostgreSQL、MinIO、Elasticsearch、Kibana、Grafana 和 Loki 只允许内网或管理网访问。
- 当前仓库没有 FastAPI 和前端的生产 Dockerfile，不要使用 `uvicorn --reload` 或 Vite 开发服务器上线。
- 当前 `docker-compose.yml` 中 PostgreSQL、Redis 和 MinIO 仍是硬编码的开发账号。生产环境必须将其改为由密钥系统/环境变量注入，或改用独立受管服务；只修改业务 `.env` 不会改变容器内部账号。

### 5.2 上线顺序

```bash
cd /opt/audit-mind/server
cp .env.example .env
# 编辑 .env 和 docker/*/.env，替换所有占位值
uv sync --frozen --no-dev
uv run alembic upgrade head

cd /opt/audit-mind/web
npm ci
npm run lint
npm run test
npm run build
```

推荐发布顺序：

1. 备份 PostgreSQL 和 MinIO；升级索引前保存当前 ES 配置。
2. 检查 PostgreSQL、Redis、MinIO、Elasticsearch、MinerU 和模型服务。
3. 停止旧 Worker 接收新任务，并等待在途任务结束。
4. 只由一个部署实例执行 `uv run alembic upgrade head`。
5. 首次部署运行 `uv run python -m scripts.manage_users create auditmind`（用户名可自定义）。
6. 启动新 Worker，然后启动或滚动更新 FastAPI。
7. 发布 `web/dist` 并重载 Nginx。
8. 检查健康接口、Worker 日志，并跑通一条真实法规和审计链路。

### 5.3 systemd 示例

`/etc/systemd/system/auditmind-api.service`：

```ini
[Unit]
Description=AuditMind FastAPI
After=network-online.target

[Service]
WorkingDirectory=/opt/audit-mind/server
EnvironmentFile=/opt/audit-mind/server/.env
ExecStart=/opt/audit-mind/server/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8181 --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/auditmind-worker.service`：

```ini
[Unit]
Description=AuditMind Dramatiq Worker
After=network-online.target

[Service]
WorkingDirectory=/opt/audit-mind/server
EnvironmentFile=/opt/audit-mind/server/.env
ExecStart=/opt/audit-mind/server/.venv/bin/dramatiq app.worker --processes 1 --threads 4
Restart=always
RestartSec=5
TimeoutStopSec=7200

[Install]
WantedBy=multi-user.target
```

启用服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now auditmind-api auditmind-worker
sudo systemctl status auditmind-api auditmind-worker
```

Worker 并发数不能只按 CPU 决定，还要结合 MinerU/GPU 吞吐、模型限流、数据库连接池和 Redis 状态压测后调整。

### 5.4 Nginx 示例

```nginx
server {
    listen 443 ssl http2;
    server_name audit.example.internal;

    root /opt/audit-mind/web/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8181/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_read_timeout 700s;
        proxy_send_timeout 700s;
    }

    location /minio/ {
        proxy_pass http://minio.internal:9000/;
        # 必须与后端生成预签名 URL 时使用的 MINIO_ENDPOINT Host 一致。
        proxy_set_header Host minio.internal:9000;
        proxy_request_buffering off;
        proxy_read_timeout 1800s;
    }
}
```

`/api` 必须关闭代理缓冲并设置足够长的读取超时，否则法规助手 SSE 可能中途断开。MinIO 预签名会包含 Host；代理改写 Host 会导致 `SignatureDoesNotMatch`。

### 5.5 定时维护任务

生产环境应设置随机 `SCHEDULER_ACCESS_TOKEN`，由 XXL-JOB 等内部调度器携带 `X-Internal-Token` 定时调用超时维护接口。调度器必须先判断 Redis 锁，只有没有活跃执行者且任务确实超过超时时间时才允许回收。

具体维护路径以当前 OpenAPI 或 `app/api/*_maintenance.py` 为准，不要从公网开放这些接口。

### 5.6 ES 索引升级

PostgreSQL 是真实数据，Elasticsearch 是可重建检索副本。Embedding 维度、Mapping 或分词配置发生不兼容变化时，新建版本索引：

```bash
uv run python scripts/rebuild_regulation_index.py \
  --target-index auditmind-regulation-chunks-v3

uv run python scripts/rebuild_regulation_rule_index.py \
  --target-index auditmind-regulation-rules-v2
```

脚本成功后更新 `.env` 中的索引名，并同时重启 API 和 Worker。不要让两个进程使用不同索引版本。

## 6. 关键注意事项

1. **Worker 不能漏启**：API 只负责投递长任务，Worker 未运行时任务不会推进。
2. **API 和 Worker 配置必须一致**：尤其是数据库、Redis、MinIO、ES 索引、Embedding 维度和模型地址。
3. **不要暴露基础设施端口**：生产环境的 5432、6379、9000、9001、9200、5601、30000、3100 和 12345 都应限制在内网。
4. **不要提交密钥**：`.env`、`docker/*/.env`、Token、API Key、上传文件和生产数据都不能进入 Git。
5. **不要执行 `docker compose down -v`**：`-v` 会删除命名卷中的 PostgreSQL、Redis、MinIO、ES 和日志数据。
6. **应用启动采用 fail-fast**：PostgreSQL、Redis、Elasticsearch 或 MinIO 不可用时 FastAPI 会拒绝启动，这是预期行为。
7. **Bucket 只在启动时创建**：业务上传流程不会临时创建 Bucket。应用账号必须拥有目标 Bucket 所需权限。
8. **失败不自动清理 MinIO**：孤立对象允许后续人工维护，避免异常清理误删可恢复文件。
9. **生产必须使用 HTTPS**：同时设置 `AUTH_COOKIE_SECURE=true`，否则 Refresh Token Cookie 的安全属性不正确。
10. **Redis 必须持久化并设置有限网络超时**：它同时承担队列、锁和 Refresh Token；网络半开不能无限阻塞。
11. **索引数据不是事实数据**：法规或规则不是 `READY` 时不可检索；ES 数据丢失后应从 PostgreSQL 重建。
12. **先备份再迁移或升级**：至少备份 PostgreSQL 和 MinIO，并定期验证恢复流程。
13. **生产镜像固定版本**：上线时不要长期使用 `latest` 标签，应固定 MinIO 等镜像版本并在测试环境验证升级。

## 7. 验证与排障

### 7.1 基础检查

```powershell
docker compose ps
docker compose logs --tail 100 postgresql redis minio elasticsearch kibana
curl.exe http://127.0.0.1:8181/health
```

健康接口会检查 PostgreSQL、Redis、Elasticsearch 和 MinIO。返回失败时先修复对应依赖，不要只重启 API。

### 7.2 Worker 检查

- 启动日志必须出现 `Worker process is ready for action`。
- 检查 Worker 是否读取了正确的 `.env`，尤其是远程 `REDIS_URL`，不要误连 `localhost`。
- 任务上传成功但状态长期不变化时，先检查 Worker 是否运行和 Dramatiq 队列是否积压。
- Worker 异常退出后由业务重试和维护接口恢复，不依赖 Dramatiq 自动重试外部高成本任务。

### 7.3 完整验证

后端检查：

```powershell
uv run pyright
uv run ruff check app scripts test
uv run pytest
```

`pyrightconfig.json` 默认检查 `app/` 和 `scripts/`；`pyright`、`ruff` 或 `pytest` 任一失败都应阻止发布。

前端检查：

```powershell
npm run lint
npm run test
npm run build
```

上线前还必须手工跑通：登录、上传规则知识、等待规则生成、法规助手问答、创建 PDF/Markdown 审计、查看证据定位和失败重试。

## 8. 备份重点

- **PostgreSQL**：定期执行 `pg_dump` 并验证恢复，它保存全部业务状态。
- **MinIO**：为 Bucket 配置版本化、复制或对象级备份，并尽量与数据库备份处于相近时间点。
- **Elasticsearch**：可用快照缩短恢复时间，也可从 PostgreSQL 使用重建脚本恢复。
- **Redis**：Compose 已启用 AOF；生产建议使用主从、哨兵或托管高可用方案。
- **配置与密钥**：使用密钥管理系统备份，不能写入镜像、仓库或普通部署日志。
