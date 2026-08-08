# E005 编译事务恢复、硬超时与严格一致性 — 任务事实记录

> Date: 2026-08-07（记录起草）/ 2026-08-08（Task 14 最终验证） | Task: E005 | 状态: 代码任务 1–12 已实施并通过各任务审查，Phase 3 文档任务（Task 13）完成；Task 14 已新鲜执行最终 R1–R8 矩阵、Windows/POSIX 进程树证据、全量离线门禁与数据清单复核（见第 9 节）；PR、Codex review 与合并待用户授权

## 1. 任务身份

- Base（INITIAL_HEAD）: `eecf4addaca453a0b29c1a447018453411d750f2`
- Branch: `fix/e005-compile-transaction-recovery`（目标集成分支: `dev`；实施起点 origin/dev = `fbc1fb8ffa582dc516b6150c5df549dadf5b2ac6`）
- Worktree: `D:\administrator\Desktop\大模型产品化\port-knowledge-base-e005`
- 设计规格: `docs/superpowers/specs/2026-08-06-e005-compile-transaction-recovery-design.md`
- 实施计划: `docs/superpowers/plans/2026-08-06-e005-compile-transaction-recovery-implementation.md`
- SDD ledger（控制器权威任务/提交历史）: `%TEMP%\port-knowledge-base-e005-sdd\progress.md`（仓库外）
- Python 解释器: `D:\ProgramData\anaconda3\python.exe`（Python 3.12.7；本机安装需要 `PYTHONUTF8=1`，GBK locale 解码问题）

## 2. 问题与根因

- E004 已建立 API 来源编译闭环（统一调度、compiling 预写、失败回滚、脱敏错误元数据），但回滚状态只存在于内存：API 进程崩溃或被强制终止时，活动事务、半更新产物与后台任务全部失联，重启后留下半成品状态；
- 后台编译无硬超时：LLM 调用挂死可无限阻塞全局串行调度锁，并残留子进程树；
- 上传摄入 `ingest_file()` 采用“先写业务文件、失败再删”的补偿模式：补偿前崩溃会留下孤立 `raw` 文档；
- 根因：编译与上传缺少持久化的事务边界、唯一提交点和崩溃恢复依据。

## 3. 控制器新鲜基线（2026-08-06，全部新鲜运行）

- `pip install -r requirements.txt`: exit 0；
- `npm ci`: exit 0；
- 真实数据清单: `originals/ raw/ wiki/ meta/` 共 76 文件捕获至 `$env:TEMP\e005-data-before.json`；
- 后端离线 pytest（无密钥、黑洞代理）: **307 passed**, 7 pre-existing warnings（pkg_resources deprecation）, exit 0, 86s；
- 前端 Jest: 17 suites / **140 tests passed**, exit 0；
- Next.js build: exit 0。

## 4. 分任务提交与审查历史

提交与审查结论以 SDD ledger 为准；下表为 ledger 记录的权威事实。表中测试数为各任务实施会话报告的数值（红绿及回归均在当时新鲜运行），非控制器复核新鲜数。

| 任务 | 内容 | 提交 | 审查 |
| --- | --- | --- | --- |
| Task 1 | `durable_fs` + `runtime_guard`（耐久写入、实例锁、运行时目录守卫） | `c7bf7bf` | SPEC PASS, QUALITY APPROVED |
| Task 2 | Manifest + 快照存储（七项产物白名单、SHA-256 校验） | `ddaa90c` | SPEC PASS, QUALITY APPROVED |
| Task 3 | `process_tree`（Windows/POSIX 进程树枚举、优雅期、终止） | `fb0567d` | SPEC PASS, QUALITY APPROVED |
| Task 4 | 恢复引擎 + 离线 CLI（启动分类恢复、inspect/recover） | `f0cfec3` + `8008bc9`（fix round 1/5：unreadable-meta fail-closed、非空 unsafe-intake-path 测试） | 修复后复审 ALL FINDINGS ADDRESSED |
| Task 5 | 编译执行器 + 硬超时（子进程编译、提交点、超时终止与回收） | `0f121c5` + `97f2c30`（fix round 1/5：超时 ROLLBACKING 迁移失败时 best-effort 终止+回收+recovery_required；reap 入 try/finally） | 修复后复审 ALL FINDINGS ADDRESSED |
| Task 6 | 纯 `prepare_ingest`（摄入与发布解耦，API/CLI 边界） | `5822a15` | SPEC PASS, QUALITY APPROVED |
| Task 7 | 上传 intake staging + intake.yaml journal 精确撤销 | `ccd3142` | SPEC PASS, QUALITY APPROVED |
| Task 8 | lifespan 启动恢复 + readiness 门禁（fail-closed 默认 503） | `e569511` | SPEC PASS, QUALITY APPROVED |
| Task 9 | 重编译/删除 API 合同接入事务（生产编译路径接通） | `748c943` + `60650f6`（fix round 1/5：未接受回滚经 ERROR_CODE_UNACCEPTED 字节级恢复 pre-bind meta、迁移失败返回 503） | 3-arg `_schedule_compile` 偏差经审查 ACCEPTED；修复后 ALL FINDINGS ADDRESSED |
| Task 10 | 两阶段上传 API + R8 六窗口崩溃恢复测试 | `07def5b` | SPEC PASS, QUALITY APPROVED, 隔离审计 PASS |
| Task 11 | 前端错误类型 + 固定文案（CompileErrorCode 穷举编译期强制） | `d4886af` | SPEC PASS, QUALITY APPROVED |
| Task 12 | wiki 轮询 interrupted 提示（历史不弹、恰一次恢复 toast、陈旧响应守卫） | `35ff805` | SPEC PASS, QUALITY APPROVED |
| Task 13 | 文档、环境合同、任务记录（本文件） | 以 git log 为准 | — |

### Phase 门禁（控制器新鲜运行）

- Phase 1 门禁: **PASS** — 离线套件 7 文件 **181 passed**, exit 0；独立整阶段审查 SPEC COMPLIANCE PASS + QUALITY & SECURITY APPROVED，GO；
- Phase 2 门禁: **PASS** — 控制器新鲜离线完整后端 pytest **499 passed**, 7 pre-existing warnings, exit 0，运行后零污染；独立整阶段审查 SPEC PASS + QUALITY APPROVED，GO。

## 5. Task 10 隔离事件与补救（如实记录）

2026-08-07，Task 10 实施代理在 RED 阶段停滞，其草稿 R8 崩溃测试直接对**真实仓库**驱动了上传流程（隔离破坏），污染真实 `raw/`（`doc_20260807_001/002` 的 txt+meta 共 4 个文件）并向 `wiki/log.md` 追加 2 条记录；`originals/` 与 `.runtime` 未受影响。

控制器补救（全部新鲜验证）：

1. 删除 4 个任务新建的 raw 文件；
2. 经 `git checkout` 从 HEAD 字节级还原 `wiki/log.md`（CRLF smudge，17653 字节）；
3. 依据 before-manifest 还原 `mtime_ns`；
4. 对照 `e005-data-before.json` 验证 size + sha256 + mtime_ns 全部一致；
5. `git status` 对 `wiki/log.md` clean。

代理的合法 WIP 保留（`tests/test_api.py` 修改、`tests/test_e005_crash_recovery.py` 新建），随后以明确的隔离修复要求恢复执行；最终修复为 `_patch_all_path_constants`（api.main 六项 + scripts.ingest 五项路径常量 + 沙箱 logger + fail-closed 包含断言），并为 `tests/test_api.py` fixtures 增补 `INDEX_FILE` 隔离；审查者复核隔离审计 PASS（无残留真实仓库可达性），控制器在提交后独立验证 clean status、无新增 raw、无 `.runtime`。

## 6. 环境合同（Task 13 新增 `.env.example`）

```text
COMPILE_TRANSACTION_DIR=.runtime/compile-transactions
COMPILE_TIMEOUT_SECONDS=1800
COMPILE_TERMINATION_GRACE_SECONDS=5
```

生产环境必须把事务目录放在与 raw/wiki/meta 同等级的持久卷，不得使用 emptyDir 或缓存目录。

## 7. 设计规格实施澄清（Task 13 按已验证事实补充，非范围变更）

1. §11/§16：未正式接受请求的回滚经 `ERROR_CODE_UNACCEPTED` 哨兵，对重编译把 meta 字节级恢复为绑定前快照 `source-meta-before.yaml`，不制造 error 终态；
2. §21：`_schedule_compile` 为显式三参数签名（`background_tasks, doc_id, runtime`），runtime 按请求解析，缺失时 fail-closed；
3. §12.3：`intake.yaml` journal 在每个目标耐久发布后追加，作为回滚唯一权威；“先发布后 journal”存在已记录的上报不足窗口；
4. §24.6：R8 六窗口在测试中经可 monkeypatch 的命名边界注入，生产无恢复绕过开关。

## 8. 已知边界与保留风险

- **单实例合同**：恢复、硬超时与门禁只保证单 API 实例；不覆盖多 worker / 多宿主写入；
- **外部 CLI 无协调**：`scripts/` CLI 并发摄入/编译不与 API 事务协调（非目标，设计 §3）；
- **非目标（设计 §3）保留**：多副本部署、跨主机锁、共享 YAML 通用事务框架（风险 6 仍保留）、外部向量库、真实 LLM UAT 声明；
- **POSIX 证据（Task 14 已补齐进程树与耐久/实例锁部分）**：进程树 killpg/session 路径、fsync 耐久写入与 portalocker 实例锁已由 Task 14 在本地 WSL Ubuntu-22.04 真实执行（详见第 9 节）；POSIX fsync 与锁的 CI 复核仍由 ubuntu-latest 全量套件承担；
- **跨进程 portalocker 语义未实测**（两个真实 uvicorn 实例，设计 §6.1 接受，OS 级 LockFile）；
- **LF-vs-CRLF 披露**：Task 6 起 `publish` 以 LF 二进制写 raw txt（旧文本模式在 Windows 写 CRLF）；消费方均已规范化，char_count 与磁盘字节一致（潜在修复，如实披露）；
- **Task 5 设计级残留**：meta 在 COMMITTED 翻转前已规范化 compiled，翻转失败会在启动时回滚一次有效编译（符合任务书顺序，属设计残留非缺陷）；
- **ledger 登记的 deferred minors 摘要**（完整清单见 SDD ledger）：durable_fs 重读校验失败时目标状态未知；probe 固定文件名仅单实例安全；Manifest 迁移无内部锁（互斥由 COMPILE_EXECUTION_LOCK 负责）；`failure.original_message` 原文存储（脱敏责任在写入方）；`stage_upload` 全文件内存缓冲（峰值约 2–3 倍，流式变体留后续任务）；journal 读取 UnicodeDecodeError 未捕获（fail-closed 方向）；journal 损坏重写时条目丢弃（泄漏方向）；请求线程回滚不持 EXECUTION 锁（任务书认可，单活动不变量下安全）；前端若干测试断言弱点与报告口径瑕疵（机制归因、子断言强度，均为测试侧 cosmetic）。

## 9. 最终验证（Task 14 新鲜执行，2026-08-08）

执行环境：worktree 根目录；后端全部运行为离线环境（`OPENAI_API_KEY=""`、`EMBEDDING_API_KEY=""`、黑洞代理 `http://127.0.0.1:9`）；解释器 `D:\ProgramData\anaconda3\python.exe`（Python 3.12.7）；每次测试运行后均新鲜执行 `git status --short` 与 `.runtime` 残留检查，结果仅为本任务范围文件、无 raw/wiki/meta/originals/.runtime 污染。

- 最终 R1–R8 完整崩溃恢复矩阵（`tests/test_e005_crash_recovery.py`，14 项）：`python -m pytest tests/test_e005_crash_recovery.py -v` → **13 passed, 1 skipped（POSIX 平台门禁项）, 7 pre-existing warnings, exit 0**。R1–R7 由 Task 14 补齐为确定性集成场景（tmp 仓库 + 短生命周期本地进程，重启一律调用生产入口 `recover_startup`）：
  - R1 SCHEDULED 崩溃（已绑定、进程未启动）：重启回滚 + meta `error/interrupted` + 活动字段清除 + 事务目录清理，产物保持事务前字节；
  - R2 RUNNING 崩溃（3 项产物半成品 + 真实父+子遗留进程树）：重启验证身份并终止整树（双 pid 退出经 deadline 断言），半成品逐字节恢复；
  - R3 提交点前崩溃（子进程成功、Manifest 仍 RUNNING、进程已退出）：成功形态产物被撤销，逐字节回滚 + interrupted；
  - R4 提交点后崩溃（COMMITTED）：`recovered == []`，仅验证并清理事务目录，业务目录（raw/wiki/meta/originals）逐字节零变化，无 error 终态；
  - R5 回滚中再次崩溃：monkeypatch `api.compile_transactions.durable_write_bytes` 耐久写入边界，2 个目标真实恢复后第 3 次调用抛专用 `_InjectedCrash`；再启动从 ROLLBACKING 幂等完成，第三次启动零副作用；
  - R6 损坏恢复依据（快照 00 字节翻转）：严格阻断（`manifest integrity` blocker），事务目录保留证据，业务与 `.runtime` 逐字节不变，meta 保持 compiling；
  - R7 双崩溃（RUNNING 进程已退出 → 恢复进入 ROLLBACKING → 第 2 个恢复目标写入边界再次注入崩溃）：第三次启动逐字节恢复全部七项 + interrupted + 验证清理，原始失败原因保留；
  - R8 六上传窗口（Task 10 实现）：6 项参数化全部保持绿色，注入机制与断言未改动；
- Windows 进程树终止最终证据：`python -m pytest tests/test_process_tree.py::test_verify_and_terminate_real_process_tree -v` → **1 passed, exit 0**（真实父+子树整树终止）；
- POSIX 进程树与 fsync 证据（本地 WSL Ubuntu-22.04 真实执行，Python 3.10.12 / pytest 9.1.1，用户级安装 pytest/psutil/PyYAML/portalocker，离线环境变量同上）：
  - `python3 -m pytest tests/test_e005_crash_recovery.py -v -k 'not r8'` → **8 passed, exit 0**（R1–R7 + `test_posix_real_process_tree_group_termination`，killpg/session 路径真实执行；R8 六项因 WSL 未装 fastapi 而 deselect，其 POSIX 复核由 CI 全量套件承担）；
  - `python3 -m pytest tests/test_process_tree.py -q` → **10 passed, exit 0**；
  - `python3 -m pytest tests/test_durable_fs.py tests/test_runtime_guard.py tests/test_compile_transactions.py tests/test_compile_recovery.py tests/test_compile_jobs.py -q` → **153 passed, exit 0**（POSIX fsync 耐久写入与 portalocker 实例锁证据）；
  - Windows 本地运行 `test_posix_real_process_tree_group_termination` 报告为 skipped（平台门禁，符合预期，不替代本平台自身真实树测试）；
- 目标后端组（任务书 Step 2 十文件）：`python -m pytest tests/test_durable_fs.py tests/test_runtime_guard.py tests/test_compile_transactions.py tests/test_process_tree.py tests/test_compile_recovery.py tests/test_compile_jobs.py tests/test_upload_intake.py tests/test_ingest.py tests/test_api.py tests/test_e005_crash_recovery.py -q` → **291 passed, 1 skipped, 7 pre-existing warnings, exit 0**；
- 完整后端门禁：`python -m pytest tests/ -q` → **506 passed, 1 skipped, 7 pre-existing warnings（pkg_resources deprecation）, exit 0（116s）**；
- 前端 Jest：`npm test -- --runInBand` → **17 suites / 155 tests passed, exit 0**；
- ESLint：`npx eslint src tests` → **exit 1（如实记录）**：1 error + 5 warnings，全部位于本任务未触碰的既有文件；唯一 error 为 `frontend/tests/unit/search-progress.test.ts` 的 `@typescript-eslint/no-require-imports`（源自既有提交 `cc8d539`，先于 E005 全部前端改动；工作区 frontend 零改动）；ESLint 不是 CI required check（frontend-unit-build 仅 Jest + build），本任务合同禁止修改前端，故保留为既有债务如实上报；
- Next.js build：`npm run build` → **exit 0**；
- pip check：`python -m pip check` → **exit 1（如实记录）**：6 项冲突全部为共享 anaconda 环境的既有非项目依赖冲突（crewai 1.11.0 要求 portalocker~=2.7.0 与 pydantic~=2.11.9；opencv-python 4.13.0.92 要求 numpy>=2；streamlit 1.37.1 要求 rich<14 与 tenacity<9；typer-slim 0.23.1 要求 typer>=0.23.1），均不在 `requirements.txt` 项目依赖内；权威干净检查为 CI python-core 的新鲜安装 + `pip check`；
- compileall：`python -m compileall api scripts tests` → **exit 0**；
- before/after 数据清单对比（数据目录零污染复核）：按任务书 Step 4 原样脚本捕获 `$env:TEMP\e005-data-after.json`（76 文件，path+size+mtime_ns+sha256），与 `e005-data-before.json` 的 Get-FileHash 比对 **完全一致**（两文件 SHA-256 均为 `3B93F37884F307D60E952D633733B9C024E3D6040970BC74A1759C4A741F401E`）；
- git 范围检查：`git status --short` 仅 `tests/test_e005_crash_recovery.py` 与本文件；`git diff --check` exit 0；`git ls-files` 无 `.runtime` / `.pytest_cache` tracked 文件；`.github/workflows/ci.yml` 未改动（python-core 既有 `python -m pytest tests/ -q` 步骤自动覆盖新测试文件，任务书预期，无需修改）；
- PR / required checks / Codex review / merge / 分支与 worktree 清理：超出 Task 14 的 Git 授权范围，Task 14 未执行；待用户明确授权后由最终集成阶段处理。

## 10. 完成标准声明边界

本文档记录 Task 1–13 的已验证事实与 Task 14 的新鲜验证证据（第 9 节全部为本会话新鲜运行结果）。E005 的最终完成声明仍须待用户授权的 PR、required checks 与 Codex review 通过后给出；第 9 节的 ESLint 既有 error 与 pip check 既有环境噪声已如实上报，不构成 E005 范围缺陷。
