# FASTQ 质控流水线台（FASTQ QC Pipeline Console）

从零实现的全栈演示：上传/选择小型 FASTQ → **Actor 队列流水线**质控 → 查看阶段状态与指标；并提供**失败归因与话术聚类台**，按失败 Actor 计数、按消息前缀聚类，支持时间与样例类型（损坏/合格）切开看。

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.11 · FastAPI · SQLAlchemy · PostgreSQL |
| 流水线 | `ParseActor` → `QualityHistActor` → `NContentActor` → `ReportActor`（asyncio.Queue） |
| 前端 | Vue 3 · Vite · Quasar · 中文 UI · nginx `/api` 反代 |
| 基建 | docker compose（db / backend / seed / frontend） |

## 端口

| 服务 | 地址 |
|------|------|
| Frontend | http://localhost:3184 |
| Backend API | http://localhost:8184 |
| PostgreSQL | localhost:54384 |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| `bioops` | `fastq123456` | 可提交质控作业 |
| `auditor` | `audit123456` | 只读结果，不可提交 |

## 一键启动

```bash
cd projects/09-fastq-qc-pipeline
docker compose up --build
```

镜像源：Postgres/Node/Nginx 使用 `docker.m.daocloud.io`；npm 使用 `registry.npmmirror.com`；pip 使用清华源。

启动后 seed 会写入：

- `demo-good-r1`：合格样例（可算出 `mean_quality` / `n_rate`）
- `demo-broken-malformed`：损坏样例（`ParseActor` 失败，后续阶段 skipped）

## Verification（验收）

1. 打开 http://localhost:3184 ，用 `bioops` / `fastq123456` 登录。
2. **样例库** 看到 2 条样例 → 选合格样例 **提交质控作业**。
3. 作业详情页看到四个 Actor 阶段均为成功，指标卡出现 `reads` / `mean_quality` / `n_rate`。
4. 再跑损坏样例：`ParseActor` = failed，其余 = skipped。
5. 退出，用 `auditor` / `audit123456` 登录：可看历史与详情，提交作业接口返回 403 / 前端无提交入口。
6. 健康检查：`curl http://localhost:8184/api/health`

### 失败归因台验收（核心口令）

多跑几次损坏样例（样例库选 `demo-broken-malformed` 重复提交 3+ 次）后，打开顶部 **失败归因台**：

1. 看到 **ParseActor 失败计数**，计数等于损坏作业次数；因 ParseActor 失败而 skipped 的后续阶段**不计入**。
2. 展开 ParseActor，看到按失败消息前缀归一化的**消息簇**（行号/实际输入值已归一，如「第 # 行分隔符必须以 + 开头，实际为:」），簇大小随次数增长，并展示最近作业。
3. 点簇内「作业 #N」直接进入该作业原详情页。
4. 用起止日期、样例类型（损坏/合格）筛选——过滤全部在服务端完成；点「下载摘录」得到当前筛选结果的 UTF-8 CSV，含
   `job_id, actor_name, stage_order, stage_status, cluster_prefix, message, sample_id, sample_name, is_broken, created_by, job_created_at, stage_started_at, stage_finished_at` 字段。
5. 两个账号（`bioops` / `auditor`）均可只读查看归因台与下载摘录；归因台不提供任何写操作入口。

## API

- `POST /api/auth/login`
- `GET  /api/health`
- `GET  /api/samples`
- `POST /api/jobs` `{ "sampleId": 1 }` 或 `{ "fastqText": "..." }`
- `GET  /api/jobs`
- `GET  /api/jobs/{id}`
- `GET  /api/jobs/{id}/stages`
- `GET  /api/attribution/failures?start_date=&end_date=&is_broken=true|false` — 按 Actor 计数 + 消息前缀簇（两角色只读）
- `GET  /api/attribution/failures/{actor}/jobs?cluster_prefix=...&start_date=&end_date=&is_broken=` — 簇内全部失败作业
- `GET  /api/attribution/export?start_date=&end_date=&is_broken=` — 当前筛选结果 CSV 摘录下载

归因口径：仅统计 `job_stages.status='failed'`；`skipped`（因上游失败跳过）不计入失败归因。日期按作业创建时间、含起止当天；`is_broken` 经样例关联过滤，自定义输入不参与损坏/合格分档。

## 本地单测（可选）

```bash
cd backend
pip install -r requirements.txt
pytest -q
```

覆盖：畸形 FASTQ 在 `ParseActor` 失败；正常样例产出 `mean_quality`。

## 目录结构

```
09-fastq-qc-pipeline/
  PRD.md
  README.md
  docker-compose.yml
  backend/
    Dockerfile
    seed.py
    data/{good,broken}.fastq
    app/
      main.py api.py auth.py models.py schemas.py
      attribution.py   # 失败归因聚合与 CSV 摘录
      pipeline/{actors,runner}.py
    tests/{test_actors,test_attribution}.py
  frontend/
    Dockerfile nginx.conf
    src/pages/{Login,Samples,JobSubmit,JobDetail,JobHistory,Attribution}Page.vue
```
