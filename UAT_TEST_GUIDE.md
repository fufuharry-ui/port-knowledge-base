# 知识库系统:自动化测试与 UAT 验收指南

> 本文档区分四层测试与各自的证据要求。所有声称都以新鲜运行为准(无 mock 伪装、无弱化断言)。
> 个人知识工作者端到端 UAT 见文末"四、隔离真实栈 UAT"。

## 0. 环境准备

```bash
cd d:\administrator\Desktop\大模型产品化\知识库研究
pip install -r requirements.txt          # 生产 + 测试依赖
```

- Python 解释器:**`D:\ProgramData\anaconda3\python.exe`**(不要依赖 PATH 上的 `python`——Windows 会解析到 WindowsApps 桩并报 "Python was not found")。
- 离线单测**无需** API Key;真实栈 UAT 需根目录 `.env`(见 `.env.example`):
  ```ini
  OPENAI_API_KEY=sk-...
  OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
  COMPILE_MODEL=qwen-plus   # ONTOLOGY_MODEL / RELATE_MODEL / SEARCH_MODEL 同
  ```
- 生产 CORS / 部署密钥由运维单独配置;本地 dev 允许 3000/3001 两个回环端口(见 `api/main.py`)。

## 一、离线单测(pytest)— 确定性逻辑

```bash
D:\ProgramData\anaconda3\python.exe -m pytest tests/ -v
```

- 覆盖 ingest/compile/search/relate/consistency/doc_admin 与 `api/main.py` 契约。
- LLM 全 mock,无网络、无 Key。`tests/conftest.py` 把每个引擎的路径常量 + 全局 logger 重定向到 `tmp_path`,**绝不污染真实 `raw/`、`wiki/`、`meta/`、`originals/`**。
- 预期:**241 passed**(基线 235 + Task 1 审计隔离守卫 1 + Task 6 上传/目录契约 5)。

## 二、前端单测(Jest)

```bash
cd frontend && npm test -- --runInBand
```

- 预期:**99 passed**,无 `Unknown option` 配置告警(`setupFilesAfterEnv`)。

## 三、Mocked UI E2E(Playwright,快速 UI 回归)

```bash
cd frontend && npm run test:e2e
```

- 后端 mock 的 UI 回归(页面/交互/可视化),不启动真实 FastAPI;用于捕捉 UI 倒退。预期:既有 mocked 用例保持绿色。

## 四、隔离真实栈 UAT(Playwright live)— 端到端业务证据

> 这是唯一能证明"用户真实旅程跑通"的层。真实 FastAPI + 真实 Next + 真实 LLM(限量),数据落在隔离副本。

```bash
cd frontend && npm run test:e2e:live
```

### 它做什么

- `tests/uat/live_server.py` 把 `api/`+`scripts/`+`raw/`+`wiki/`+`meta/`+`originals/` 复制到 `.uat/runtime/`,从副本启动真实 FastAPI(8001);Next dev 起在 3001(`NEXT_PUBLIC_API_BASE=http://127.0.0.1:8001`)。
- 启动前对源数据 76 文件逐个 SHA-256 指纹;UAT 后 `tests/uat/verify_source_unchanged.py` 逐文件比对——**源数据零污染**是硬验收。
- `.uat/` 为运行产物(gitignored);证据落到 `UAT测试日志/<YYYYMMDD>/evidence/`(JSON reporter、截图、source-unchanged.txt)。

### 用例(八字段见 `UAT测试日志/<YYYYMMDD>/uat-matrix.md`)

`personal-kb-loop.spec.ts` 串行跑一条完整链:上传→权威回执→仪表盘编译可见→多轮带引用问答→引用穿梭+实体探索→重复/非法文件诚实处理→重编译→删除清理。P0/P1 必须 100%。

### LLM 成本与环境

- 仅:一个短文档编译 + 一次重编译 + 两轮 QA。`compile_ingested_task` 用 `sys.executable`(anaconda)而非 PATH 上的 `python`。
- 单 worker、0 retry,避免共享状态竞争与重复计费。
- 外部 LLM 超时/不可用 → 标"环境未验证",**不伪造通过**(NFR: <10K 字编译 ≤60s、首问 ≤25s)。

### Windows 注意

- 服务器终止后 `.uat/runtime` 可能因文件句柄延迟释放而 rmtree 失败;`live_server._rmtree_robust` 已重试 6×1s。
- Next 16 默认只允许 localhost 访问 dev 资源;`next.config.ts` 的 `allowedDevOrigins` 已放开 `127.0.0.1`。

## 五、验收阈值(本轮)

| 项 | 阈值 |
|---|---|
| pytest | 241 passed,源数据指纹不变 |
| Jest | 99 passed,无配置告警 |
| 链路 lint/build | touched code 0 ESLint error,`next build` exit 0 |
| Mocked E2E | 既有用例保持绿色 |
| Live UAT | P0/P1 100%(P2 ≥60%),源 76 文件 SHA-256 前后一致 |
