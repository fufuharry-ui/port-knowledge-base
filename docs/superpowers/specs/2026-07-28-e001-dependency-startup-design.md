# E001 Dependency and Startup Baseline Design

> Task: E001 | Base: `origin/dev` @ `8c183a03ba2172193bb82d6bf07a9256663d14af` | Date: 2026-07-28
> Branch: `chore/e001-dependency-startup-baseline` | Author role: 技术调研与设计工程师

---

## Decision

**Approved approach: A — dependency and startup baseline, full pytest audit only.**

E001 只解决四件事：

1. `requirements.txt` 覆盖所有真实直接运行依赖与测试依赖；
2. 干净环境（CI runner、本地临时 venv）能够完整安装并通过 `pip check`；
3. `scripts.embedding_client`、`api.main`、`app.main` 在无任何 API Key、无 `.env` 的环境中能够完成模块导入，两套 FastAPI 入口的健康检查返回 200 且 `status=ok`；
4. CI 的 `python-core` job 增加启动冒烟门禁，required check 名称不变。

完整 `pytest tests/ -q` 在 E001 中**只执行、如实审计、按根因分类记录**，不通过 mock、跳过项或生产代码修改强行变绿，不加入 required gate。Embedding 真正可选化（`layer1_filter` 无条件构造 `VectorEngine` 的已知缺陷）属于 E002，不在本任务处理。

---

## Context and Evidence

以下事实均来自对 `origin/dev` @ `8c183a0` 的只读调研（AST 扫描 + 逐文件阅读）。

### 代码结构事实

- 两套 FastAPI 入口并存（CLAUDE.md §4.2 已记录）：
  - `api/main.py`：当前前端实际使用的后端，健康检查为 `GET /api/v1/health`；
  - `app/main.py`：模块化候选实现，健康检查为 `GET /health`，路由在 `app/routers/`。
- `requirements.txt` 当前显式声明 11 条：`openai>=1.30.0`、`pyyaml>=6.0`、`pdfplumber>=0.10.0`、`python-docx>=1.1.0`、`readability-lxml>=0.8.1`、`fastapi>=0.111.0`、`uvicorn[standard]>=0.29.0`、`python-multipart>=0.0.9`、`jieba>=0.42.1`、`httpx>=0.27.0`、`pytest-asyncio>=0.23.0`；另有 2 条注释掉的可选项（`rank-bm25`、`python-dotenv`）。
- CI（`.github/workflows/ci.yml`）现有三个 job：`repository-integrity`、`python-core`（Python 3.11，install → compileall → 6 个确定性核心测试文件）、`frontend-unit-build`。E000 已确立这三个为初始 required checks，且明确 `python-core` 不是完整后端验证，由 E001/E002 扩展。

### 依赖缺口事实（AST 扫描证据）

对 `api/`、`app/`、`scripts/`、`tests/` 全部 `.py` 文件做 AST import 扫描（含函数内懒加载 import），得到以下缺口：

1. **`requests` 未声明**：`scripts/embedding_client.py:2` 顶层 `import requests`。该模块被 `scripts/search.py:167`（`VectorEngine.__init__`）懒加载。当前能装上是因为 `openai`/`httpx` 之外某个传递依赖恰好带入，属于偶然安装。
2. **`sse_starlette` 未声明**：`api/main.py:13` 顶层 `from sse_starlette.sse import EventSourceResponse`，用于 `/api/v1/search/stream` 与 `/api/v1/qa`。`api.main` 导入即需要它，当前无任何显式声明。
3. **`pytest` 未声明**：`tests/` 下 24 个测试文件直接 `import pytest`（含 `tests/conftest.py:12`）。当前由 `pytest-asyncio` 的传递依赖带入。
4. **`pydantic` 未显式声明**：`api/main.py:12`（`BaseModel`）与 `app/schemas.py:7`（`BaseModel, Field`）直接 import。当前由 `fastapi` 传递带入。生产代码直接 import 的第三方包应显式声明。

### 已声明且无缺口的事实

- `openai`：`scripts/compile.py:55`、`scripts/relate.py:49`、`scripts/search.py:36` 均为函数内懒加载 `from openai import OpenAI`；已声明。
- `yaml`（PyYAML）：广泛使用；已声明 `pyyaml>=6.0`。
- `jieba`：`api/main.py:48`（预热）、`app/utils/tokenizer.py:37`（函数内）、`scripts/search.py:127`（函数内）；已声明。
- `pdfplumber` / `docx`（python-docx）/ `readability`（readability-lxml）：仅在 `scripts/ingest.py:73/94/124` 的解析器函数内懒加载；均已声明。
- `httpx`：生产代码不直接 import，但 `fastapi.testclient.TestClient`（`tests/test_api*.py`）运行时需要；已声明。
- `python-multipart`：不直接 import，但 FastAPI `UploadFile = File(...)` 运行时需要；已声明。
- `uvicorn`：仅 `app/main.py:48` 的 `__main__` 入口使用；已声明。

### 已声明但当前未使用的事实

- `pytest-asyncio>=0.23.0`：全仓扫描未发现 `import pytest_asyncio`、`@pytest.mark.asyncio`、`asyncio_mode` 配置或 `anyio` 使用。它当前是未使用的声明。E001 **不删除**（删除属于与启动基线无关的变更，且可能影响未来异步测试），仅在实施报告中如实记录该观察，是否移除由用户另行决定。

### 启动链路事实

**`api.main` 导入期行为**（`api/main.py`）：

- 第三方导入：`fastapi`、`pydantic`、`sse_starlette`、`yaml`，以及 `scripts.{ingest,search,ontology,lint,consistency}`（这些 scripts 模块顶层只 import 标准库 + `yaml`，重依赖全部懒加载）；
- **导入期读取 `.env`**：第 34-42 行，按 `Path(__file__).resolve().parent.parent / ".env"` 读取，`os.environ.setdefault` 语义，真实环境变量优先；
- **导入期预热 jieba**：第 47-52 行，`jieba.cut` 触发词典加载，异常被吞（jieba 缺失不影响启动）；
- **导入期创建目录**：第 74 行 `ORIGINALS_DIR.mkdir(exist_ok=True)`，路径派生自 `__file__`；
- 导入期**不**实例化 LLM/Embedding 客户端（`openai` 在 `get_llm_client()` 内懒加载，`EmbeddingClient` 在 `VectorEngine.__init__` 内懒加载）；
- 健康接口 `GET /api/v1/health`（第 260-294 行）：只读——读 `wiki/index.yaml` 计数、检查 `OPENAI_API_KEY` 是否存在、读 jieba 预热标志、检查 `GLOBAL_ONTOLOGY_FILE.exists()`；不调 LLM、不写文件、即使依赖缺失也返回 200 + `status=ok`；
- 无 `OPENAI_API_KEY`、无 `EMBEDDING_API_KEY` 时可导入、健康检查可返回 200。

**`app.main` 导入期行为**（`app/main.py` + `app/routers/`）：

- 导入 `app.routers.{ingest,search,knowledge,qa}` → 进而导入 `app.config`（仅定义 `Settings` 类，`get_settings()` 是 `lru_cache` 函数，**导入期不实例化**）、`app.schemas`（pydantic 模型）、`app.utils.tokenizer`（jieba 函数内懒加载）、`app.utils.background`（仅 logging）、`scripts.ingest`、`scripts.search`；
- 导入期**不**读取 `.env`（`config._load_dot_env` 只在 `Settings()` 实例化时执行，即首个 `Depends(get_settings)` 请求时）；
- 导入期**不**创建目录（`Settings.__init__` 的 mkdir 同样延迟到实例化；`/health` 不使用 `Depends`）；
- 导入期无网络调用、无数据写入；
- 健康接口 `GET /health`（第 41-43 行）：返回常量 `{"status": "ok", "version": "2.0.0"}`，无任何 I/O；
- 无密钥时可导入、健康检查可返回 200；
- `allow_origins=["*"]` 的 CORS 是已知生产隐患，**不属于 E001 范围**。

**`.env` 读取风险**（冒烟测试必须做源码快照隔离的根因）：

- `api/main.py:35` 与 `scripts/embedding_client.py:8` 都按**自身 `__file__` 推导项目根**读取 `.env`；
- 在开发 worktree 中直接 `import api.main` 会读取用户真实 `.env` 并把真实密钥注入进程环境，无法证明"无密钥可启动"，也违反 CLAUDE.md §9（测试不得访问用户真实 `.env`）；
- 把源码复制到无 `.env` 的临时快照后再导入，是从机制上（而非约定上）保证无密钥、无真实数据的唯一可靠方式，且不需要修改生产代码增加"测试专用开关"。

**Embedding 与完整 pytest 的边界**：

- `scripts/search.py:250` `layer1_filter` 无条件执行 `VectorEngine(docs)` → `EmbeddingClient()` → 无 `EMBEDDING_API_KEY` 时抛 `RuntimeError`（`scripts/embedding_client.py:29-36`）；
- 这意味着干净环境下所有触及 `layer1_filter` 的测试（如 `tests/test_hybrid_search.py`、`tests/test_search.py::TestLayer1Filter` 等）会失败；这是 CLAUDE.md §5 已记录的已知缺陷，修复属于 E002；
- E001 对该类失败只登记、分类、归入 E002，不修复、不 mock 掩饰。

---

## Goals

1. 单一 `requirements.txt` 显式覆盖全部真实直接运行依赖与测试依赖，不依赖传递依赖偶然安装；
2. CI 干净环境（Python 3.11）与本地临时 venv（Python 3.12）均能 `pip install -r requirements.txt` 成功且 `python -m pip check` 通过；
3. 新增 `tests/test_startup_smoke.py`：在临时源码快照、无 `.env`、无密钥、代理指向黑洞的子进程中，验证 `scripts.embedding_client`、`api.main`、`app.main` 可导入，且 `GET /api/v1/health` 与 `GET /health` 均返回 200 + `status=ok`；
4. CI `python-core` job 增加 `pip check` 与启动冒烟两个步骤，job 名称保持 `python-core` 不变；
5. 完整 `pytest tests/ -q` 被执行并如实审计（exit code、passed/failed/skipped、按根因分类），Embedding 相关失败登记到 E002；
6. 现有 6 个确定性核心测试文件（当前 99 项）继续全部通过；
7. `CLAUDE.md` 仅更新"依赖与启动基线"相关的已知风险事实（第 17 节第 2、4 条）。

## Non-goals

- 不统一 `api/` 与 `app/` 两套后端；
- 不修改任何生产代码（`api/`、`app/`、`scripts/`）；
- 不修复 Embedding 可选降级（E002）；
- 不修复多轮引用全局状态（E003）；
- 不修改 CORS 策略、后台任务状态记录、错误处理；
- 不修改 `tests/conftest.py`、`tests/test_search.py` 或任何现有测试；
- 不引入锁文件（pip-tools/poetry/uv 等）或依赖管理平台迁移；
- 不升级任何与本任务无关的包；
- 不实例化真实 LLM/Embedding 客户端，不调用真实网络；
- 不把完整 pytest 加入 required gate；
- 不修改 `.env.example`、`frontend/`、`package.json`、`package-lock.json` 或真实知识数据。

---

## Dependency Inventory

### 扫描方法

只读 AST 脚本（`ast.parse` + `ast.walk`，覆盖函数内懒加载 import），扫描 `api/**/*.py`、`app/**/*.py`、`scripts/**/*.py`、`tests/**/*.py`，将顶层模块分为：标准库 / 仓库本地模块 / 第三方运行依赖 / 第三方测试依赖 / 可选依赖。非字符串 grep。

### 映射表

| 顶层模块 | 使用位置 | 类型 | requirements 当前是否显式声明 | 建议处理 |
|---|---|---|---|---|
| `fastapi` | `api/main.py`、`app/main.py`、`app/routers/*`、4 个 `tests/test_api*.py` | 运行依赖 | 是（`fastapi>=0.111.0`） | 保持不变 |
| `pydantic` | `api/main.py:12`、`app/schemas.py:7` | 运行依赖 | 否（fastapi 传递带入） | **新增 `pydantic>=2.7.0`** |
| `sse_starlette` | `api/main.py:13` | 运行依赖 | 否 | **新增 `sse-starlette>=2.1.0`** |
| `requests` | `scripts/embedding_client.py:2`（顶层） | 运行依赖 | 否 | **新增 `requests>=2.31.0`** |
| `yaml`（PyYAML） | `api/main.py`、`app/routers/*`、7 个 scripts 模块、14 个测试文件 | 运行依赖 | 是（`pyyaml>=6.0`） | 保持不变 |
| `openai` | `scripts/compile.py:55`、`scripts/relate.py:49`、`scripts/search.py:36`（均函数内懒加载） | 运行依赖 | 是（`openai>=1.30.0`） | 保持不变 |
| `jieba` | `api/main.py:48`、`app/utils/tokenizer.py:37`、`scripts/search.py:127` | 运行依赖 | 是（`jieba>=0.42.1`） | 保持不变 |
| `pdfplumber` | `scripts/ingest.py:73`（懒加载） | 运行依赖 | 是（`pdfplumber>=0.10.0`） | 保持不变 |
| `docx`（python-docx） | `scripts/ingest.py:94`（懒加载） | 运行依赖 | 是（`python-docx>=1.1.0`） | 保持不变 |
| `readability`（readability-lxml） | `scripts/ingest.py:124`（懒加载） | 运行依赖 | 是（`readability-lxml>=0.8.1`） | 保持不变 |
| `uvicorn` | `app/main.py:48`（`__main__` 入口） | 运行依赖 | 是（`uvicorn[standard]>=0.29.0`） | 保持不变 |
| `python-multipart` | 无直接 import；FastAPI `UploadFile = File(...)` 运行时必需 | 运行依赖 | 是（`python-multipart>=0.0.9`） | 保持不变 |
| `httpx` | 无直接 import；`fastapi.testclient.TestClient` 运行时必需 | 测试运行依赖 | 是（`httpx>=0.27.0`） | 保持不变 |
| `pytest` | 24 个 `tests/*.py`（含 `conftest.py`） | 测试依赖 | 否（pytest-asyncio 传递带入） | **新增 `pytest>=8.2.0`** |
| `pytest_asyncio` | 无（全仓无 import、无 marker、无 asyncio_mode 配置） | 测试依赖（当前未使用） | 是（`pytest-asyncio>=0.23.0`） | 保持不变；如实记录"声明但未使用"，是否移除由用户另行决定 |
| `rank-bm25`、`python-dotenv` | 无 | 可选（当前用内置实现） | 注释状态 | 保持注释，不启用 |
| `live_server` | `tests/uat/verify_source_unchanged.py` | 仓库本地模块（`tests/uat/live_server.py`，文件名无 `test_` 前缀，pytest 不收集） | 不适用 | 不处理 |
| 标准库（`os`/`json`/`pathlib`/`hashlib`/`subprocess`/`tempfile`/`typing` 等） | 全部模块 | 标准库 | 不适用 | 不处理 |
| 本地包（`api`、`app`、`scripts`、`tests`、`sample_data`、`conftest`） | 全部模块 | 仓库本地 | 不适用 | 不处理 |

### 声明原则（E001 实施必须遵守）

- 生产代码直接 import 的第三方包必须显式声明，不依赖传递依赖偶然安装；
- 测试代码直接 import 的测试工具同样显式声明；
- 不为未实际使用的包新增依赖；
- 保持单一 `requirements.txt`，不引入锁文件或依赖管理平台迁移；
- 不升级无关包，不动既有版本下限；
- 版本下限必须同时兼容 CI Python 3.11 与本地 Python 3.12；
- 不盲目锁死当前最新版本（只用 `>=` 下限，不打 `==` 钉版）。

### 建议新增依赖行及依据

```text
requests>=2.31.0        # scripts/embedding_client.py 顶层 import；2.31.0 起完整支持 Python 3.12
sse-starlette>=2.1.0    # api/main.py 顶层 import（SSE 端点）；2.1.x 起与 pydantic v2 / fastapi>=0.111 兼容线对齐
pytest>=8.2.0           # tests/ 24 个文件直接 import；8.2.x 支持 Python 3.11/3.12
pydantic>=2.7.0         # api/main.py 与 app/schemas.py 直接 import；fastapi>=0.111 对应 pydantic v2 兼容线
```

验证方式：实施后 `pip check` 必须无冲突；若某条下限与 `fastapi` 解析结果冲突，实施阶段以提高该条下限解决并在报告中说明，不得降低 `fastapi>=0.111.0`。

---

## Clean Source Isolation

冒烟测试**不得在开发 worktree 中直接导入** `api.main` / `app.main` / `scripts.embedding_client`，原因：

1. `api/main.py:35` 与 `scripts/embedding_client.py:8` 按自身 `__file__` 推导项目根并读取根目录 `.env`——直接导入会读取用户真实配置，把真实密钥注入测试进程；
2. 直接导入时 `api/main.py:74` 的 `ORIGINALS_DIR.mkdir` 与 `scripts/logger.py` 的 `global_logger` 都指向真实仓库目录；
3. 只有临时源码快照能从机制上同时保证"真正无 `.env`、真正无密钥、真正不碰真实知识数据"，且不需要为测试修改生产代码。

隔离方案（测试在 `tmp_path` 中执行）：

1. 从当前工作副本**只复制** `api/`、`app/`、`scripts/` 三个目录到 `tmp_path` 快照；
2. **不复制** `.env`、`.git`、`raw/`、`wiki/`、`meta/`、`originals/` 的真实内容、`tests/`、`frontend/`；
3. 在快照中创建最小目录骨架：`raw/`、`wiki/`、`meta/ontology/`、`meta/relations/`、`originals/`；
4. 写入最小 `wiki/index.yaml`，内容为 `documents: []`；
5. 使用 `sys.executable` 启动子进程，`PYTHONPATH` 指向快照根；
6. 子进程环境中**删除** `OPENAI_API_KEY`、`EMBEDDING_API_KEY`、`OPENAI_BASE_URL`、`EMBEDDING_BASE_URL`；
7. 子进程环境**设置** `PYTHONUTF8=1`、`HTTP_PROXY=http://127.0.0.1:9`、`HTTPS_PROXY=http://127.0.0.1:9`（代理指向黑洞端口，任何意外外联立即失败而非静默成功）；
8. 子进程脚本依次执行：
   - `import scripts.embedding_client`
   - `import api.main`
   - `import app.main`
   - `TestClient(api.main.app).get("/api/v1/health")`
   - `TestClient(app.main.app).get("/health")`
   - 以 JSON 把两个响应的状态码与 body 打印到 stdout 供父进程断言；
9. 父进程断言：
   - 子进程 exit code 为 0；
   - 两个响应均为 200 且 `status == "ok"`；
   - 子进程 stderr 不含要求 API Key 的异常痕迹；
   - 快照之外无文件变化（测试前后对工作副本相关目录做只读校验）。

导入 `scripts.embedding_client` 时其模块级 `_load_dot_env()` 会执行，但快照中无 `.env`，函数直接返回，不注入任何密钥——这正是快照方案要证明的行为。

---

## Startup Smoke Test Design

新增文件 `tests/test_startup_smoke.py`，架构如下：

```text
tests/test_startup_smoke.py
├── _copy_source_snapshot(tmp_path)     # 复制 api/ app/ scripts/ + 最小目录骨架 + index.yaml
├── _child_env(snapshot)                # 构造无密钥、黑洞代理、PYTHONUTF8=1、PYTHONPATH=快照 的环境
├── _CHILD_SCRIPT (str)                 # 子进程内联脚本：三个 import + 两个 health 请求 + JSON 输出
├── test_embedding_client_importable_without_keys()
├── test_api_main_health_ok_without_env()
└── test_app_main_health_ok_without_env()
```

设计决策与理由：

- **子进程隔离而非进程内 import**：`.env` 加载、jieba 预热、`ORIGINALS_DIR.mkdir` 都是导入期副作用，进程内 import 会污染当前 pytest 进程且无法干净卸载；子进程让每个用例获得全新解释器；
- **`sys.executable` 而非 `"python"`**：与 `api/main.py:195-201` 已记录的 Windows 事实一致（PATH 上的 `python` 可能是 WindowsApps 桩）；
- **TestClient 而非真实 uvicorn 端口**：健康检查语义在 ASGI 层即可完整验证，避免端口占用、防火墙与启动超时等不稳定因素；`httpx` 已在依赖中；
- **黑洞代理 `127.0.0.1:9`**：端口 9（discard）在本机通常无监听，连接立即被拒绝；健康检查本身不发网络请求，代理只是防回归保险——一旦未来有人在导入链或健康接口引入外联，冒烟立刻失败；
- **三个用例共用一个快照构造函数，各自独立 `tmp_path`**：用例间无共享状态，任一失败不影响其余；
- **不 mock 任何生产模块**：冒烟测的是真实导入链，mock 会使证据失效。

实施时该文件不得 import 任何 `api`/`app`/`scripts` 模块到 pytest 父进程（只复制文件、拼子进程命令），父进程只依赖标准库 + `pytest`。

---

## Clean Virtual Environment Validation

实施阶段采用两层干净环境验证：

### 层 1：GitHub Actions 干净环境（权威门禁）

GitHub runner 本身即全新环境。`python-core` job 依次执行：

1. `actions/checkout@v4`；
2. `actions/setup-python@v5`（Python 3.11，保留 pip cache）；
3. `python -m pip install -r requirements.txt`；
4. **新增** `python -m pip check`；
5. `python -m compileall -q api app scripts tests`（保留）；
6. **新增** `python -m pytest tests/test_startup_smoke.py -q`；
7. 现有 6 个确定性核心测试文件（保留，命令不变）。

### 层 2：本地临时 venv（Python 3.12 交叉验证）

PowerShell 命令框架（venv 建在仓库之外的临时目录，不进入 Git 状态；不硬编码任何密钥）：

```powershell
$venv = Join-Path $env:TEMP "e001-clean-venv"
if (Test-Path $venv) { Remove-Item -Recurse -Force $venv }
& "D:\ProgramData\anaconda3\python.exe" -m venv $venv
& "$venv\Scripts\python.exe" -m pip install -r requirements.txt
& "$venv\Scripts\python.exe" -m pip check
$env:PYTHONUTF8 = "1"
& "$venv\Scripts\python.exe" -m pytest tests/test_startup_smoke.py -q
& "$venv\Scripts\python.exe" -m pytest tests/test_ingest.py tests/test_compile.py tests/test_relate.py tests/test_ontology.py tests/test_consistency.py tests/test_doc_admin.py -q
Remove-Item -Recurse -Force $venv
Remove-Item Env:PYTHONUTF8
```

规则：

- 是否先 `python -m pip install --upgrade pip` 由实施时 venv 自带 pip 能否正常解析依赖决定——能装则不升级，不无理由全局升级；
- venv 位于 `$env:TEMP`，在仓库之外，天然不进入 `git status`；验证结束后删除；
- 该 venv 中**不创建** `.env`，验证"无密钥可导入、可健康检查"正是目的；
- 本地 venv 验证是补充证据；合并门禁以 CI 层 1 为准。

---

## CI Integration

只修改现有 `python-core` job，不新增 job、不更名任何 job 或 step 的 required context。计划变更（`Install dependencies` 之后插入）：

```yaml
      - name: Verify installed dependencies
        run: python -m pip check
```

并在 `Compile Python sources` 之后、`Run deterministic core tests` 之前插入：

```yaml
      - name: Run startup smoke tests
        run: python -m pytest tests/test_startup_smoke.py -q
```

保留不变：

- job 名 `python-core`（dev 分支保护规则的 required check，更名会使保护失效）；
- `compileall` 步骤；
- 6 个确定性核心测试步骤；
- `repository-integrity` 与 `frontend-unit-build` 两个 job 的全部内容。

禁止事项：

- 不在 workflow 中引入任何密钥或 secret；
- 不使用 `continue-on-error`；
- 不用 `|| true` 等方式隐藏任何步骤失败；
- 不运行真实 LLM/Embedding 调用；
- 不把完整 `pytest tests/` 加入当前 required gate；
- 不弱化现有三个 required checks。

---

## Full Pytest Audit Policy

### 必须绿色的合并门禁（required）

- `pip install -r requirements.txt`；
- `python -m pip check`；
- `python -m compileall -q api app scripts tests`；
- `python -m pytest tests/test_startup_smoke.py -q`；
- 6 个确定性核心测试文件（当前 99 项）；
- 现有 `repository-integrity`、`frontend-unit-build` 保持不变。

### 只审计、不要求绿色

实施阶段在无 `.env`、无有效 LLM/Embedding Key 的环境（本地临时 venv 内的干净快照，或 CI 等价条件）执行一次：

```powershell
python -m pytest tests/ -q
```

审计要求：

- 记录 exit code 及 passed / failed / skipped 计数；
- 按根因分类每个失败，至少区分：
  - **Embedding 缺失类**：`layer1_filter` 无条件构造 `VectorEngine` → `EmbeddingClient` 无 Key 抛 `RuntimeError` 导致的失败 → 登记到 E002，不单独阻断 E001；
  - **依赖或启动类**：ImportError、ModuleNotFoundError、健康检查失败、缺依赖导致的 collection 错误 → 属于 E001 处理范围，必须修复；
  - **其他类**：与依赖、启动无关的失败 → 如实记录并分类说明，登记为后续债务，不在 E001 修复；
- 不调用真实 API；审计运行同样处于无密钥环境；
- 不通过 mock、skip、xfail 强行变绿；
- 不把非零 exit code 隐藏为成功；
- 审计结果原文（计数 + 分类）写入实施任务文档 `docs/dev/tasks/E001-dependency-startup-baseline.md`。

禁止：

- `continue-on-error` 或 `|| true`；
- 修改 `tests/test_search.py` 等现有测试让生产缺陷消失；
- 在报告中声称完整 pytest 通过，除非实际 exit code 为 0。

---

## Planned File Changes

实施阶段预计只修改或创建以下 5 个文件：

| 文件 | 变更内容 |
|---|---|
| `requirements.txt` | 新增 4 行：`requests>=2.31.0`、`sse-starlette>=2.1.0`、`pytest>=8.2.0`、`pydantic>=2.7.0`（含简短注释）；其余行不动 |
| `tests/test_startup_smoke.py` | 新建；源码快照 + 子进程冒烟（见上文设计） |
| `.github/workflows/ci.yml` | `python-core` job 插入 `pip check` 与 startup smoke 两个步骤；其余不动 |
| `docs/dev/tasks/E001-dependency-startup-baseline.md` | 新建实施任务文档，含完整 pytest 审计结果 |
| `CLAUDE.md` | 仅更新第 17 节中与依赖、启动基线相关的已知风险事实（第 2、4 条的状态），不重写其他章节 |

本设计文档本身：

- `docs/superpowers/specs/2026-07-28-e001-dependency-startup-design.md`（唯一在本设计轮创建的文件）

明确不修改：

- `api/`、`app/`、`scripts/`（生产代码零变更）；
- `tests/conftest.py`、`tests/test_search.py` 及所有现有测试；
- `.env.example`、`frontend/`、`package.json`、`package-lock.json`；
- `raw/`、`wiki/`、`meta/`、`originals/` 真实知识数据。

若实施中发现必须修改上述之外的文件：立即停止、说明原范围不足的原因、列出拟新增文件、等待用户重新授权（CLAUDE.md §3）。

---

## Security and Data Isolation

- 冒烟测试与完整 pytest 审计均在无 `.env`、无密钥环境执行；子进程显式删除四个 Key/URL 环境变量；
- 黑洞代理（`HTTP_PROXY`/`HTTPS_PROXY=http://127.0.0.1:9`）保证任何意外外联立即失败；
- 快照只含源码，不含 `raw/`、`wiki/`、`meta/`、`originals/` 真实内容；`api.main` 导入期的 `ORIGINALS_DIR.mkdir` 落在快照内；
- 工作副本的真实数据目录在测试前后做只读校验（存在性与内容不被触碰），任何变化立即停止；
- CI workflow 不引入任何 secret；
- 设计文档与实施文档均不含真实 Key、真实 Base URL 或个人凭据；`.env.example` 中的占位值（`sk-your-key-here`）不作为真实凭据引用；
- 不为测试在生产代码中增加任何开关、环境变量分支或测试专用路径。

---

## Failure Handling

| 条件 | 处理 |
|---|---|
| 干净环境 `pip install` 失败 | E001 阻断，排查依赖声明 |
| `pip check` 失败 | E001 阻断，调整冲突包的下限（不得降低 `fastapi>=0.111.0`） |
| 任一后端无法在无密钥环境导入 | E001 阻断 |
| 任一健康接口非 200 或 `status != "ok"` | E001 阻断 |
| 冒烟子进程发生网络请求（黑洞代理被命中的证据） | E001 阻断，定位并消除外联 |
| 6 个核心测试出现回归 | E001 阻断 |
| 完整 pytest 因 Embedding 缺失失败 | 登记 E002，不单独阻断 E001 |
| 完整 pytest 出现与依赖或启动有关的新失败 | E001 阻断 |
| 真实数据目录（`raw/`、`wiki/`、`meta/`、`originals/`）发生变化 | 立即停止，报告 |
| 需要修改生产代码才能继续 | 停止并重新评估任务边界，报告用户等待决定 |

---

## Acceptance Criteria

| # | 验收标准 | 验证方法 |
|---|---|---|
| 1 | 单一 `requirements.txt` 覆盖所有实际直接运行与测试依赖 | AST 扫描复核 + `pip check` |
| 2 | fresh install 成功 | CI `python-core` install 步骤 + 本地临时 venv install |
| 3 | `pip check` 成功 | CI 与本地 venv 各执行一次，exit code 0 |
| 4 | `scripts.embedding_client` 无 Key 可导入 | `test_startup_smoke.py` 子进程用例 |
| 5 | `api.main` 无 Key 可导入 | 同上 |
| 6 | `app.main` 无 Key 可导入 | 同上 |
| 7 | `GET /api/v1/health` 返回 200 且 `status=ok` | TestClient 断言 |
| 8 | `GET /health` 返回 200 且 `status=ok` | TestClient 断言 |
| 9 | 冒烟不读取用户真实 `.env` | 快照内无 `.env`；子进程环境无密钥仍能成功即为证据 |
| 10 | 冒烟不访问真实知识数据 | 快照只含空骨架；工作副本数据目录只读校验 |
| 11 | 冒烟不调用网络 | 黑洞代理 + 健康接口本身只读；子进程成功即无阻塞外联 |
| 12 | 99 项现有核心测试继续通过 | CI 与本地各跑一次 6 文件核心套件 |
| 13 | 完整 pytest 被执行并如实记录 | 实施任务文档中的 exit code + 计数 + 根因分类 |
| 14 | CI `python-core` 名称不变 | diff 审查 `.github/workflows/ci.yml` |
| 15 | Embedding 真实运行逻辑不变 | `git diff` 中 `scripts/` 零变更 |
| 16 | 业务 API 行为不变 | `git diff` 中 `api/`、`app/` 零变更 |

---

## Risks and Follow-up

- **E002（Embedding 可选化）**：`layer1_filter` 无条件构造 `VectorEngine` 是完整 pytest 干净环境失败的主因；E002 需实现"未配置或调用失败 → 自动退化 BM25/关键词检索"的契约并补离线测试。E001 的完整 pytest 审计为 E002 提供失败清单输入；
- **E003（多轮引用状态）**：前端 `ChatPanel.tsx` 全局 citations 覆盖历史回答的已知缺陷，与 E001 无依赖关系；
- **双后端分叉**：`api/` 与 `app/` 健康检查路径、CORS 策略、默认模型（`gpt-4o` vs `qwen-plus`）均不一致；统一是独立架构任务，E001 只保证两者都能无密钥启动；
- **`pytest-asyncio` 声明但未使用**：保留声明；若未来确认无异步测试计划，可由独立任务移除；
- **版本下限演进**：E001 只设 `>=` 下限不打钉版；若未来出现上游破坏性升级导致 CI 安装漂移，是否引入锁文件由独立任务评估；
- **本地与 CI Python 版本差**：CI 3.11、本地 3.12；下限选择已考虑双版本兼容，但仅在两个环境实测通过后才算闭环。

## Alternatives Rejected

- **方案 B（E001 内顺手修复 Embedding 降级）**：拒绝。修复 `layer1_filter`/`embedding_client` 属于生产代码变更，需要失败测试先行、降级契约验证和检索行为回归证据，与"依赖与启动基线"是两种不同的风险类别；混在一起会让 E001 的 diff 同时触及依赖、CI、测试和生产代码，违反 CLAUDE.md §3 的任务边界原则，也使审查无法聚焦。Embedding 修复已由 E002 独立承接；
- **方案 C（E001 内把完整 pytest 强行变绿并纳入门禁）**：拒绝。在无密钥干净环境下完整 pytest 存在 Embedding 缺失导致的真实失败，要变绿只能 mock 生产缺陷或修改生产代码——前者让测试证据失效（违反 CLAUDE.md §9"不得使用未配置的通用 MagicMock 链条作为业务成功证据"），后者越权修改生产代码。如实审计 + 分类登记比虚假绿色更有工程价值，且完整 pytest 纳入 required gate 会在 E002 完成前永久阻塞 dev 集成。

---

*本设计已通过自检：无未决项；与方案 A 一致；不含 Embedding 降级内容；不要求修改生产代码；全部验收标准均有验证方法；文件范围与任务合同一致；不含真实密钥、URL 或个人凭据。*
