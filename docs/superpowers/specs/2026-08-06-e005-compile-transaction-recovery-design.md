# E005 编译事务恢复、硬超时与严格一致性设计

- **任务编号**：E005
- **设计状态**：书面规格已修订，待实施计划执行前复核
- **基线分支**：`dev`
- **基线提交**：`fbc1fb8ffa582dc516b6150c5df549dadf5b2ac6`
- **设计分支**：`docs/e005-compile-transaction-recovery-design`
- **日期**：2026-08-06
- **范围属性**：设计文档；本提交不修改业务代码、测试、依赖或真实知识库数据

## 1. 背景与问题

E004 已完成 API 来源编译的状态、错误、单进程串行和失败回滚闭环：上传与重编译统一进入后台包装器，接受任务前写入 `compiling`，失败时恢复摘要、索引、本体与关系产物，前端通过三秒轮询展示稳定错误类别。

E004 仍有以下风险：

1. 快照位于 `TemporaryDirectory`，API 异常退出后恢复依据消失。
2. `subprocess.run()` 没有硬超时，模型调用、`compile.py` 或 `relate.py` 可长期挂起。
3. API 在写入终态前退出时，文档可能永久停留在 `compiling`。
4. `compile.py` 会再启动 `relate.py`；只终止父进程可能留下继续写共享 YAML 的子进程。
5. 没有启动恢复协议，无法区分未提交、已提交、回滚中断、损坏快照与孤立状态。
6. `threading.Lock` 只约束单个 Python 进程，多 worker 可并发写同一批共享文件。
7. 上传当前先发布原文件和 `raw` 文件，再准备编译；崩溃或事务准备失败可能留下用户未明确接受的半成品文档。
8. 运行期间若回滚或补偿失败，系统缺少统一的“不再返回不可信结果”门禁。

E005 将 E004 的临时失败回滚升级为：

```text
单 API 实例
+ 单全局活动事务
+ 持久化快照与 Manifest
+ 30 分钟硬超时
+ 完整进程树终止
+ 启动恢复
+ 严格就绪门禁
+ 两阶段上传摄入发布
```

## 2. 目标

E005 必须实现：

1. 任何进入 `status=compiling` 的 API 来源任务，都有可持久化、可验证的事务清单和七项编译产物快照。
2. 同一时刻最多一个非终态编译事务，不允许多个文档先进入 `compiling` 后排队。
3. 默认硬超时为 1800 秒；超时后终止完整进程树，确认无残留写进程后再回滚。
4. API 启动前同步恢复未提交事务；恢复成功后才监听请求。
5. Manifest 原子进入 `COMMITTED` 是唯一成功提交点；提交点前崩溃一律回滚。
6. 回滚过程幂等；回滚中再次崩溃后可继续恢复。
7. 损坏清单、损坏快照、多个非终态事务、进程身份不可验证或孤立 `compiling` 均严格阻止启动。
8. 运行期间出现无法证明一致性的故障时进入 `recovery_required`，停止全部知识库业务接口。
9. 新增 `interrupted` 文档错误码，区分服务生命周期中断与普通编译失败。
10. 保持上传、重编译、删除和目录查询的主要成功响应形状，不公开内部事务信息。
11. 上传采用“两阶段摄入发布”：解析和生成 `raw` 候选先在事务 staging 内完成，编译事务准备成功后才发布到业务目录。
12. 提供只读检查和安全恢复 CLI，不提供跳过校验或删除证据的旁路。
13. 自动化测试不调用真实 LLM、不访问公网、不污染真实知识库数据。

## 3. 非目标

E005 不包含：

- 多 worker、多容器副本或多主机写入；
- Redis、数据库、分布式锁、分布式事务或持久化任务队列；
- 自动重新提交中断任务或自动重试 LLM；
- 百分比进度、步骤心跳、取消、优先级或任务详情页；
- 以心跳延长 30 分钟绝对硬超时；
- `repair`、`start-api-anyway` 或其他绕过恢复的启动参数；
- 外部手工执行 `python -m scripts.compile` 与 API 事务协调；
- 全部共享 YAML 写入路径的通用事务化重构；
- 真实模型 Live UAT；
- 网络文件系统、磁盘损坏、控制器丢写或整机断电下的绝对持久性承诺。

## 4. 已确认决策

1. 采用持久化事务恢复，不采用仅改遗留状态的轻量方案。
2. 启动恢复严格阻断，不能安全恢复时不监听。
3. `COMPILE_TIMEOUT_SECONDS` 默认 1800，非法值阻止启动。
4. 超时先尽力优雅终止整个进程树，等待 5 秒，再强制终止残留进程。
5. 新增 `interrupted` 文档终态错误码。
6. 正式限定单 API 实例；第二个 worker 或实例启动失败。
7. 默认事务目录为 `.runtime/compile-transactions`，生产必须使用持久化存储。
8. 存在孤立 `compiling` 时阻止启动，不自动改状态。
9. Manifest 的 `COMMITTED` 是唯一提交点，不增加 `commit.marker`。
10. 不采用任务心跳延时，也不采用带病启动模式。
11. 终态事务目录经过语义与哈希验证后才清理。
12. 恢复 CLI 复用生产恢复库。
13. `SCHEDULED` 不表示可重新排队，而表示任务已不可撤销地进入执行生命周期；任何退出均按中断事务处理。
14. 上传不再先发布业务文件后补偿，而是先在 staging 中完成解析和候选文件生成，事务准备成功后统一发布。

## 5. 总体架构

E005 分为六个职责边界：

1. **配置与实例门禁**：解析配置、验证事务目录、持有跨平台实例锁。
2. **耐久文件工具**：同目录临时文件、文件 `fsync`、原子替换、POSIX 父目录 `fsync`、哈希验证。
3. **事务存储**：创建 staging、七项快照、Manifest、状态迁移和终态验证。
4. **编译执行器**：独立进程组、身份记录、等待、超时及进程树终止。
5. **恢复协调器**：启动扫描、幂等回滚、孤立状态检测、终态清理和 CLI。
6. **上传摄入协调器**：上传暂存、纯解析、候选 `raw` 生成、原子发布和编译事务绑定。

正常重编译链路：

```text
请求
→ 调度锁确认 ready 且无活动事务
→ staging 中生成七项快照
→ 发布 PREPARED
→ 文档绑定 job 并进入 compiling
→ Manifest 进入 SCHEDULED
→ 后台任务取得执行锁
→ 进程树启动，Manifest 进入 RUNNING
→ 成功语义验证
→ Manifest 原子进入 COMMITTED
→ 终态验证并清理
```

正常上传链路：

```text
请求
→ 调度锁确认 ready 且无活动事务
→ 上传字节写入 intake staging
→ 解析并在 staging 生成 proposed original/raw/meta
→ 分配 doc_id
→ 生成编译事务七项快照并发布 PREPARED
→ 原子发布 original/raw/meta 到业务目录
→ meta 绑定 job 并进入 compiling
→ Manifest 进入 SCHEDULED
→ 后续与重编译相同
```

失败或恢复链路：

```text
失败 / 超时 / 启动恢复
→ Manifest 进入 ROLLBACKING
→ 必要时终止并确认完整进程树退出
→ 验证快照
→ 恢复或删除七项编译产物
→ 对上传任务撤销本轮发布的 original/raw 文件
→ 验证恢复结果
→ 写文档 error 终态（已正式接受的任务）
→ Manifest 进入 ROLLED_BACK
→ 终态验证并清理
```

## 6. 部署模型与配置

### 6.1 单实例合同

运行模型：

```text
一个 API 进程 + 一个全局活动编译事务
```

API lifespan 开始时使用 `portalocker` 获取：

```text
.runtime/api-instance.lock
```

生命周期：

```text
获取实例锁
→ 验证配置与目录
→ 启动恢复
→ 开始服务
→ 运行期间持续持锁
→ lifespan 结束释放
```

第二个进程获取失败时直接终止启动。实例锁只用于拒绝第二个 API 实例，不表示支持多进程共享写。

### 6.2 配置项

```text
COMPILE_TRANSACTION_DIR=.runtime/compile-transactions
COMPILE_TIMEOUT_SECONDS=1800
COMPILE_TERMINATION_GRACE_SECONDS=5
```

规则：

- 相对路径以仓库根目录解析；
- 超时范围 60 至 86400 秒；
- 终止宽限期范围 1 至 60 秒；
- 目录必须可创建、可写、可读并支持同目录原子替换；
- `.runtime/` 加入 `.gitignore`；
- `.env.example` 和部署说明明确生产持久卷要求。

### 6.3 持久化要求

事务目录与 `raw/`、`wiki/`、`meta/` 具有同等级或更高的重启持久性，不得放在容器临时层、`emptyDir`、系统临时目录或自动清理的 cache。

启动探针验证写入、`fsync`、原子替换、重新读取和删除；挂载介质能否跨重启保留由部署合同保证。

## 7. 事务目录与 Manifest

### 7.1 目录结构

```text
.runtime/
├── api-instance.lock
├── upload-intake/
│   └── .staging-{intake_id}/
└── compile-transactions/
    ├── .staging-{job_id}/
    └── {job_id}/
        ├── manifest.yaml
        ├── source-meta-before.yaml
        ├── intake.yaml                 # 仅上传任务存在
        └── snapshots/
            ├── 00.bin
            └── ...
```

### 7.2 七项编译产物白名单

```text
wiki/{doc_id}.summary.yaml
wiki/index.yaml
meta/ontology/{doc_id}.ontology.yaml
meta/ontology/global_ontology.yaml
meta/relations/{doc_id}.relations.yaml
meta/relations/knowledge_graph.yaml
meta/ontology/entity_relations.yaml
```

恢复目标由 `base_dir + doc_id + 固定白名单`重新计算，不直接信任 Manifest 路径。

### 7.3 Manifest示例

```yaml
schema_version: 1
job_id: 01J...
doc_id: doc_001
kind: recompile        # recompile | upload
state: RUNNING

created_at: 2026-08-06T14:30:00+08:00
scheduled_at: 2026-08-06T14:30:01+08:00
started_at: 2026-08-06T14:30:02+08:00
deadline: 2026-08-06T15:00:02+08:00

timeout_seconds: 1800
termination_grace_seconds: 5
previous_document_status: compiled

published_intake:
  original_path: originals/example.pdf
  raw_text_path: raw/doc_001.txt
  raw_meta_path: raw/doc_001.meta.yaml
  published: true

process:
  pid: 12345
  create_time: 1786007401.25
  executable: C:/Python312/python.exe
  cwd: D:/repo
  command_fingerprint: scripts.compile|doc_001
  process_group_id: 12345
  platform: windows

failure:
  original_code:
  original_message:

recovery:
  last_error:
  failed_paths: []

artifacts:
  - slot: 0
    path: wiki/doc_001.summary.yaml
    existed: true
    snapshot: snapshots/00.bin
    snapshot_size: 12850
    snapshot_sha256: ...
    original_sha256: ...
```

Manifest不记录 API Key、Authorization、环境变量值、文档正文、完整LLM请求响应或未脱敏输出。

## 8. 耐久写入合同

所有关键写入复用一个耐久工具：

```text
同目录创建临时文件
→ 写完整内容
→ flush
→ fsync(文件)
→ os.replace
→ 重新读取或哈希验证
→ POSIX fsync(父目录)
```

适用于：快照、Manifest、`source-meta-before.yaml`、`intake.yaml`、事务字段、上传候选文件发布和回滚目标文件。

Windows执行文件级 `fsync` 与原子替换；若不支持等价父目录同步，不伪造承诺。E005保障进程异常退出与正常操作系统重启后的恢复，不承诺硬件或文件系统损坏场景。

## 9. 状态机与语义

```text
PREPARED
   ↓
SCHEDULED
   ↓
RUNNING ─────────→ COMMITTED
   ↓
ROLLBACKING
   ↓
ROLLED_BACK
```

- `PREPARED`：快照和正式Manifest已持久化，业务文档可能尚未绑定。
- `SCHEDULED`：业务文件已发布、文档已绑定job、后台执行已登记；**不是可重排队列状态**。它表示任务已不可撤销地进入执行生命周期，进程退出或API重启时必须回滚并标记 `interrupted`，不得重新 `add_task`。
- `RUNNING`：编译根进程已启动并记录可验证身份。
- `COMMITTED`：成功结果已通过语义验证且Manifest越过唯一提交点。
- `ROLLBACKING`：正在执行或需要继续执行幂等恢复。
- `ROLLED_BACK`：产物恢复、哈希验证、上传发布撤销（如适用）及业务终态写入均完成。

不增加 `FAILED`、`FINALIZED` 或独立 `commit.marker`。

## 10. 锁与并发合同

进程生命周期持有 `api-instance.lock`。线程锁顺序固定为：

```text
COMPILE_SCHEDULE_LOCK → COMPILE_EXECUTION_LOCK
```

规则：

- 调度与上传准备在调度锁内完成；
- 执行、进程终止、回滚与终态提交在执行锁内完成；
- 删除先持有调度锁，再非阻塞获取执行锁；
- 禁止持有执行锁后反向获取调度锁；
- 不再增加额外事务文件锁；CLI先获取同一实例锁后复用恢复库。

存在任一 `PREPARED/SCHEDULED/RUNNING/ROLLBACKING`事务时，新编译或上传返回 `409 knowledge_base_busy`。

## 11. 重编译事务准备

顺序：

```text
获取调度锁
→ 确认 service_mode=ready
→ 确认无活动事务
→ 读取并保存原meta
→ 在 .staging-{job_id} 生成七项快照
→ 验证快照大小和SHA-256
→ 原子写 PREPARED Manifest
→ 原子重命名为正式事务目录
→ 原子写meta为compiling并绑定job
→ 原子写Manifest=SCHEDULED
→ add_task
→ 返回成功
```

异常处理：

- 正式事务发布前失败：删除 staging，meta不变；
- `PREPARED`已发布但meta未绑定：启动时验证原meta后清理，不写`interrupted`；
- meta已绑定但Manifest仍为`PREPARED`：按中断事务回滚；
- `add_task`失败：请求线程立即进入回滚；
- 请求未正式接受时不制造文档错误终态。

## 12. 两阶段上传摄入发布

### 12.1 根因约束

当前 `ingest_file()`只写：

```text
originals/{file}
raw/{doc_id}.txt
raw/{doc_id}.meta.yaml
```

它不更新 `wiki/index.yaml`、本体或关系文件。E005仍不能依赖“先写再删”的补偿，因为API可在补偿前崩溃并留下孤立 `raw` 文档。

### 12.2 新的摄入边界

摄入拆为：

```python
@dataclass(frozen=True)
class PreparedIngest:
    doc_id: str
    original_name: str
    file_hash: str
    text_bytes: bytes
    meta: dict


def prepare_ingest(staged_source: Path, *, existing_doc_ids: set[str]) -> PreparedIngest:
    ...
```

`prepare_ingest()`：

- 解析上传文件；
- 计算哈希、语言和字符数；
- 分配doc_id；
- 返回候选内容；
- 不写 `originals/`、`raw/`、`wiki/` 或 `meta/`。

现有CLI `ingest_file()`可调用 `prepare_ingest()` 后按旧合同发布，保持外部CLI行为；API只使用两阶段接口。

### 12.3 上传事务顺序

```text
获取调度锁
→ 确认ready且无活动事务
→ 上传字节写到 .runtime/upload-intake/.staging-{intake_id}
→ 按file_hash检查重复
→ prepare_ingest生成候选doc_id/text/meta
→ 为该doc_id生成七项编译快照
→ 发布PREPARED编译事务，kind=upload
→ 耐久写入intake.yaml
→ 原子发布original、raw text、raw meta
→ meta绑定job并进入compiling
→ Manifest=SCHEDULED
→ add_task
→ 返回processing
```

业务目录发布必须记录每个目标是否由本请求创建。发布时若任一目标已存在，停止并回滚，不能覆盖既有文件。

### 12.4 上传失败与崩溃恢复

- `PREPARED`发布前崩溃：只有未发布的 intake staging；启动时安全清理。
- `PREPARED`已发布、业务文件未发布：清理事务与intake，不写文档错误。
- 已发布部分业务文件：根据Manifest白名单删除本请求创建的目标，不触碰既有文件。
- 已进入`SCHEDULED/RUNNING`：按正常中断回滚七项编译产物，并删除本轮上传发布的original/raw；上传未形成可用文档，因此不保留`status=error`孤儿。
- 上传事务回滚失败：保持`ROLLBACKING`并阻止启动或进入`recovery_required`。

重复文件保持现有 `skipped=true`合同，不创建编译事务。

## 13. 编译执行器与进程身份

后台任务取得执行锁后：

```text
SCHEDULED
→ 以独立进程组启动 sys.executable -m scripts.compile <doc_id>
→ 记录pid、create_time、executable、cwd和业务命令指纹
→ Manifest=RUNNING
→ 等待成功、失败或绝对硬超时
```

进程身份验证：

1. PID存在；
2. 创建时间匹配；
3. 可执行文件匹配；
4. cwd匹配；
5. 规范化业务命令指纹匹配，例如 `scripts.compile|doc_001`。

指纹不包含解释器绝对路径。PID存在但身份不匹配时不终止、不回滚，阻止启动并保留证据。

建议引入 `psutil`用于进程树枚举、创建时间、身份读取和跨平台终止。

## 14. 硬超时与进程树终止

超时从 `RUNNING`开始后的绝对时间计算，不因日志或阶段延长。

超时流程：

```text
Manifest=ROLLBACKING, reason=timeout
→ 终止完整进程树
→ 等待宽限期
→ 强制终止残留
→ 确认根进程和全部后代退出
→ 回滚
```

POSIX：独立session/process group，`SIGTERM`后等待，再`SIGKILL`。

Windows：`CREATE_NEW_PROCESS_GROUP`，能投递时先 `CTRL_BREAK_EVENT`，等待后通过 `psutil`先终止后代、再终止根进程，最后kill残留。

无法确认进程树退出时不回滚，实例进入`recovery_required`。

## 15. 唯一提交点与成功验证

成功顺序：

```text
根进程和后代全部退出
→ return code=0
→ meta状态=compiled
→ 必需产物语义验证
→ wrapper规范化compiled meta并清除活动字段
→ Manifest原子进入COMMITTED
→ 终态验证
→ 清理目录
```

提交点前崩溃一律回滚，即使文件看起来完整；提交点后崩溃只验证和清理。

必须存在并可解析：

- `wiki/{doc_id}.summary.yaml`
- `meta/ontology/{doc_id}.ontology.yaml`
- `wiki/index.yaml`
- `raw/{doc_id}.meta.yaml`

必须满足：

- summary和ontology的`doc_id`匹配；
- index中恰有一条目标文档；
- meta为`compiled`；
- doc级关系文件若存在则`doc_id`匹配；
- 全局本体、知识图谱和实体关系文件若存在则顶层结构合法。

不要求所有共享文件哈希发生变化。

## 16. 幂等回滚

顺序：

```text
Manifest=ROLLBACKING并保存原始失败原因
→ 验证全部快照
→ 恢复原本存在的文件
→ 删除原本不存在、本轮新建的文件
→ 撤销上传发布文件（kind=upload）
→ 验证恢复结果与事务前哈希一致
→ 对重编译写status=error及稳定错误码
→ 对上传删除本轮raw/meta/original，不保留孤儿文档
→ 清除活动字段
→ Manifest=ROLLED_BACK
→ 终态验证并清理
```

在回滚中再次崩溃时，下次启动继续相同操作。恢复失败时保持`ROLLBACKING`，记录失败路径，并使用`rollback_failed`对外诊断；后续恢复成功后以原始业务失败原因为最终文档错误码。

## 17. 启动恢复

顺序：

```text
获取实例锁
→ 验证配置与目录
→ 清理未发布upload/compile staging
→ 扫描正式事务
→ 恢复非终态事务
→ 验证并清理终态事务
→ 扫描全部raw meta
→ 确认无孤立compiling
→ service_mode=ready
→ 开始服务
```

正常合同最多一个非终态事务；发现两个及以上时不猜测快照顺序，直接阻止启动。

状态处理：

| 状态 | 启动动作 |
|---|---|
|`PREPARED`且业务文件未绑定|清理事务和intake，不写`interrupted`|
|`PREPARED`但业务文件已绑定|按中断事务回滚|
|`SCHEDULED`|回滚；不得重新排队|
|`RUNNING`|验证并终止遗留进程树后回滚|
|`ROLLBACKING`|继续幂等回滚|
|`ROLLED_BACK`|验证终态后清理|
|`COMMITTED`|验证提交终态后清理，不回滚|
|未知schema/state|阻止启动|

事务恢复后仍有`status=compiling`且无匹配事务时输出doc_id并阻止启动。

## 18. 终态清理合同

清理不能只看state。

`COMMITTED`清理前验证：

- Manifest schema、job和doc匹配；
- meta为`compiled`且无活动字段；
- 必需产物语义合法；
- 不存在与job绑定的`compiling`。

`ROLLED_BACK`清理前验证：

- 七项产物与事务前存在性和SHA一致；
- 上传事务的本轮业务文件已按合同撤销；
- 重编译事务meta为`error`且错误码与原始失败原因一致；
- 无活动job字段。

纯目录删除失败只记录警告，后续启动和CLI继续尝试；验证失败则不清理，并阻止启动或维持门禁。

## 19. 服务门禁与健康检查

应用内部：

```text
service_mode = ready | recovery_required
```

运行期间若进程树无法停止、快照损坏、回滚失败或上传发布撤销失败，状态单向进入`recovery_required`。所有知识库业务接口返回：

```json
{"detail":{"code":"recovery_required"}}
```

只允许：

- `GET /api/v1/health`
- `GET /api/v1/ready`

`/health`始终HTTP 200，增加`ready`和安全恢复状态；`/ready`在ready时200，在门禁时503。公共响应不暴露job、PID、路径或失败文件。

## 20. 错误码合同

### 20.1 文档终态

| 错误码 | 含义 |
|---|---|
|`llm_configuration`|模型配置或鉴权失败|
|`service_unavailable`|上游服务或网络不可用|
|`timeout`|超过硬超时|
|`document_processing`|文档内容无法处理|
|`compile_failed`|其他编译失败|
|`interrupted`|未提交事务因服务生命周期中断而恢复|
|`rollback_failed`|当前无法证明旧版本已完整恢复|

`rollback_failed`优先级最高。技术`error_message`继续脱敏、单行化并限制500字符，公共目录不投影。

### 20.2 请求级

| 错误码 | HTTP | 场景 |
|---|---:|---|
|`compile_in_progress`|409|同一文档已有活动事务|
|`knowledge_base_busy`|409|其他全局事务活动中|
|`compile_transaction_unavailable`|503|事务准备或耐久发布失败，请求未接受|
|`recovery_required`|503|实例已进入一致性门禁|

非法配置和启动恢复失败发生在监听前，不产生业务HTTP响应。

## 21. API合同

重编译成功响应保持：

```json
{"status":"recompiling","doc_id":"doc_001"}
```

上传成功响应保持：

```json
{
  "status":"processing",
  "skipped":false,
  "doc_id":"doc_001",
  "filename":"example.pdf",
  "message":"摄入成功，后台自动编译中..."
}
```

请求只有在事务正式发布、业务文件发布、meta绑定和后台任务登记全部完成后才被接受。响应不返回job_id。

删除在任一活动事务期间返回409；门禁期间所有业务接口返回503。

公共目录只投影`status`和稳定`error_code`，不投影活动job字段、PID、事务目录、快照或技术错误。

## 22. 前端合同

保持现有行为：

- `raw/compiling`每3秒轮询；
- 全部终态后停止；
- 历史error不弹Toast；
- `compiling→error`每轮只弹一次；
- 重新编译开启新失败轮；
- 过期和乱序响应不能覆盖新状态；
- 瞬时轮询失败保留上一份目录。

新增固定文案：

```text
interrupted:
编译任务因服务重启中断，旧版本已恢复，请重新编译

compile_transaction_unavailable:
编译任务暂时无法创建，请稍后重试或联系管理员

recovery_required:
知识库正在恢复或需要管理员处理，暂不可用
```

前端类型拆为文档终态错误和请求错误的联合，不新增可见状态值，不展示事务ID、进程或恢复日志。

## 23. 离线恢复CLI

新增：

```text
python -m scripts.compile_recovery inspect
python -m scripts.compile_recovery verify <job_id>
python -m scripts.compile_recovery recover <job_id>
python -m scripts.compile_recovery cleanup-terminal
```

CLI先获取同一个实例锁，复用生产恢复库。禁止提供`force-delete`、`ignore-checksum`、`skip-process-check`、`mark-resolved`或带病启动命令。

## 24. 测试矩阵

### 24.1 事务存储

- staging生成七项白名单；
- 快照大小和SHA正确；
- 正式目录原子发布；
- 未知schema/state拒绝；
- 路径越界拒绝；
- 快照损坏拒绝回滚；
- 原本不存在的目标回滚后仍不存在；
- Manifest写失败时业务状态不进入`compiling`。

### 24.2 状态机

- `PREPARED`未绑定：清理且不写`interrupted`；
- `PREPARED`已绑定：回滚；
- `SCHEDULED`：回滚且不得重新add_task；
- `RUNNING`进程不存在：回滚；
- `RUNNING`身份匹配：终止树后回滚；
- PID身份不匹配：不误杀并阻止启动；
- `ROLLBACKING`：幂等继续；
- `COMMITTED/ROLLED_BACK`：验证后清理；
- 多非终态事务：阻止启动。

### 24.3 进程树与超时

- 期限内完成正常提交；
- 达到硬超时先terminate后kill；
- 子进程派生孙进程时整树退出后才回滚；
- 残留进程存在时禁止回滚；
- PID复用和指纹不匹配均不误杀；
- Windows和POSIX各至少一个真实短生命周期进程树测试。

### 24.4 API与上传

- 重编译接受形状兼容；
- 同文档409、其他文档409；
- 事务准备失败503且原meta不变；
- 上传忙在读取/解析后但业务发布前409，业务数据目录不变；
- `prepare_ingest`不写业务目录；
- 上传发布任一步失败时撤销本请求目标；
- 上传恢复不删除既有同名文件；
- 门禁期间全部业务接口503；
- health/ready语义正确；
- 公共目录不暴露内部字段。

### 24.5 前端

- 历史`interrupted`显示但不Toast；
- `compiling→interrupted`每轮一次Toast；
- 重试后可提示新失败轮；
- 事务准备503与恢复门禁503显示固定文案；
- 轮询断线保留旧快照；
- 文案不含job、PID、路径、状态码或原始错误。

### 24.6 崩溃恢复R1—R8

**R1 SCHEDULED崩溃**：已绑定、进程未启动，重启回滚并`interrupted`。

**R2 RUNNING崩溃**：部分产物已改，重启终止遗留树并恢复。

**R3 提交点前崩溃**：子进程成功但Manifest仍RUNNING，重启回滚。

**R4 提交点后崩溃**：Manifest已COMMITTED，重启只验证和清理。

**R5 回滚中再次崩溃**：部分文件已恢复，再次启动幂等完成。

**R6 损坏恢复依据**：快照损坏时严格阻断且不修改业务文件。

**R7 双崩溃恢复**：RUNNING崩溃，启动恢复进入ROLLBACKING后再次崩溃，第三次启动逐字节恢复并清理。

**R8 上传准备与发布阶段崩溃**：分别在以下窗口注入退出：

1. intake staging写入后、`prepare_ingest`前；
2. 候选raw/meta生成后、PREPARED发布前；
3. PREPARED发布后、业务文件发布前；
4. original发布后、raw text发布前；
5. raw text发布后、raw meta/job绑定前；
6. SCHEDULED后、编译进程启动前。

每个窗口重启后必须满足：

- 无孤立doc或孤立`compiling`；
- 无半成品index、本体或关系产物；
- 不删除任何既有业务文件；
- 未正式接受的上传不保留错误文档；
- 已进入SCHEDULED的上传按中断事务完整撤销。

## 25. 数据与离线安全门禁

所有测试：

- 显式清空模型Key；
- 使用黑洞代理或网络拦截；
- 不调用真实LLM；
- 破坏性测试仅使用临时仓库；
- 对真实`originals/raw/wiki/meta`执行前后path、size、SHA-256和mtime清单比对；
- 清理测试`.runtime`产物；
- 完整pytest、Jest、ESLint、Next build和三个required checks保持绿色；
- required checks名称不变。

## 26. 迁移与兼容

首次启动：

- 无事务且无`compiling`：正常；
- E004遗留`compiling`无E005 Manifest：阻止启动并输出doc_id；
- 旧`raw/error/compiled`无需补job字段；
- 成功响应形状兼容；
- `interrupted`仍表现为`status=error`。

部署前必须审计遗留`compiling`，不得由部署脚本直接改YAML或删除证据。

## 27. 实施分阶段原则

Implementation Plan必须分为三个可独立审查阶段：

### Phase 1：事务内核

- 配置、实例锁、耐久文件工具；
- Manifest、快照、状态机、进程树和恢复库；
- 恢复CLI；
- 不接入生产API入口。

### Phase 2：API与上传接入

- lifespan启动恢复与门禁；
- 重编译、删除、health/ready；
- `prepare_ingest`两阶段上传发布；
- 上传崩溃R8和API合同测试。

### Phase 3：前端与全量门禁

- 错误码与固定文案；
- 轮询迁移测试；
- 文档、依赖、`.gitignore`、`.env.example`；
- Windows/POSIX真实进程验证、全量离线测试和数据清单。

每个阶段必须遵循TDD、独立提交和阶段性复核，不允许一次同时改完`compile.py`、`relate.py`、API和前端。

## 28. 完成标准

E005提交审查前必须同时满足：

1. 单实例锁在Windows和POSIX有效；
2. 无持久事务依据的任务不能进入`compiling`；
3. 任意时刻最多一个活动事务；
4. 30分钟默认硬超时及配置边界通过；
5. 超时和启动恢复能终止完整进程树，身份不确定时不误杀；
6. 提交点前回滚，提交点后只清理；
7. `ROLLBACKING`二次崩溃后可继续；
8. 损坏快照、未知schema、多事务和孤立状态严格阻断；
9. 上传在业务目录发布前已经拥有恢复依据；
10. R8所有上传崩溃窗口无孤立文件或文档；
11. `interrupted`、409/503和前端文案符合合同；
12. 公共API不暴露内部事务信息；
13. health/ready语义清晰；
14. CLI没有绕过安全检查的命令；
15. 真实数据清单前后完全一致；
16. 后端、前端、lint、build和CI门禁全部通过；
17. 变更不夹带后端统一或通用YAML重构。

## 29. 规格自审

- **占位符**：无待定项、空白章节或未确认产品决策。
- **一致性**：固定硬超时、严格阻断、单实例、单活动事务、唯一提交点和不自动重试相互一致。
- **SCHEDULED语义**：明确为已承诺执行且不可重新排队的中间状态。
- **上传边界**：解析和候选文件生成在staging中完成，编译事务PREPARED后才发布业务文件，不依赖崩溃后补偿猜测。
- **状态机**：`ROLLED_BACK`包含恢复、验证、上传撤销和业务终态，不需要`FINALIZED`。
- **锁顺序**：实例锁贯穿生命周期，线程锁只允许调度锁到执行锁。
- **范围**：没有引入多worker、分布式锁、数据库、自动重试、心跳延时或repair模式。
- **可测试性**：R1—R8覆盖提交窗口、进程树、双崩溃、损坏快照和上传发布窗口。
- **实施分解**：Phase 1/2/3分别覆盖事务内核、API上传接入和前端全量门禁。

本规格具备进入Implementation Plan的条件。
