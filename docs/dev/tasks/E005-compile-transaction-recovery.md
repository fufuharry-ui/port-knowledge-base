# E005 编译事务恢复、硬超时与严格一致性 — 任务事实记录

> Date: 2026-08-07（记录起草） | Task: E005 | 状态: 代码任务 1–12 已实施并通过各任务审查，Phase 3 文档任务（Task 13）完成；最终全量门禁、R1–R8 最终矩阵、数据清单复核由 Task 14 新鲜执行，本文档相应项保持 `未执行` 占位

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
- **POSIX 证据待 Task 14/CI**：进程树 killpg/session 路径与 fsync 耐久证据在本 Windows 机器上未执行，属 Task 14/CI 范围；
- **跨进程 portalocker 语义未实测**（两个真实 uvicorn 实例，设计 §6.1 接受，OS 级 LockFile）；
- **LF-vs-CRLF 披露**：Task 6 起 `publish` 以 LF 二进制写 raw txt（旧文本模式在 Windows 写 CRLF）；消费方均已规范化，char_count 与磁盘字节一致（潜在修复，如实披露）；
- **Task 5 设计级残留**：meta 在 COMMITTED 翻转前已规范化 compiled，翻转失败会在启动时回滚一次有效编译（符合任务书顺序，属设计残留非缺陷）；
- **ledger 登记的 deferred minors 摘要**（完整清单见 SDD ledger）：durable_fs 重读校验失败时目标状态未知；probe 固定文件名仅单实例安全；Manifest 迁移无内部锁（互斥由 COMPILE_EXECUTION_LOCK 负责）；`failure.original_message` 原文存储（脱敏责任在写入方）；`stage_upload` 全文件内存缓冲（峰值约 2–3 倍，流式变体留后续任务）；journal 读取 UnicodeDecodeError 未捕获（fail-closed 方向）；journal 损坏重写时条目丢弃（泄漏方向）；请求线程回滚不持 EXECUTION 锁（任务书认可，单活动不变量下安全）；前端若干测试断言弱点与报告口径瑕疵（机制归因、子断言强度，均为测试侧 cosmetic）。

## 9. 最终验证（Task 14 范围，本文档一律 `未执行`）

以下项目必须由 Task 14 新鲜执行后替换 `未执行`，本文档起草时不得预填：

- 最终 R1–R8 完整崩溃恢复矩阵新鲜运行：未执行；
- Windows 进程树终止最终证据：未执行；
- POSIX 进程树与 fsync 证据：未执行（Task 14/CI）；
- 最终完整后端 pytest / 前端 Jest / ESLint / Next build 全量门禁：未执行；
- before/after 数据清单对比（数据目录零污染复核）：未执行；
- PR / required checks / Codex review / merge / 分支与 worktree 清理：未执行。

## 10. 完成标准声明边界

本文档只记录 Task 1–13 的已验证事实与明确 `未执行` 占位；不构成 E005 整体完成声明。E005 的完成声明只能由 Task 14 的新鲜验证证据与最终 PR/完成报告给出。
