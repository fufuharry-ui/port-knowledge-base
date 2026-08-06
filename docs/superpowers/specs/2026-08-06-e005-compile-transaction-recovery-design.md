# E005 编译事务恢复、硬超时与严格一致性设计

- **任务编号**：E005
- **设计状态**：设计内容已确认，书面规格待用户审阅
- **基线分支**：`dev`
- **基线提交**：`fbc1fb8ffa582dc516b6150c5df549dadf5b2ac6`
- **设计分支**：`docs/e005-compile-transaction-recovery-design`
- **日期**：2026-08-06
- **范围属性**：设计文档；本提交不修改业务代码、测试、依赖或真实知识库数据

## 1. 背景与问题

E004 已完成 API 来源编译的状态、错误、进程内串行和失败回滚闭环：上传与重编译统一进入后台编译包装器，接受任务前写入 `compiling`，失败时恢复七类摘要、索引、本体与关系产物，前端通过三秒轮询展示稳定错误类别。

E004 仍存在以下未覆盖风险：

1. 快照位于 `TemporaryDirectory`。API 进程异常退出后，恢复依据随进程消失。
2. `subprocess.run()` 没有硬超时。模型请求、编译脚本或关系检测子进程可长期挂起并占用全局执行锁。
3. 文档进入 `compiling` 后，API 若在写入终态前退出，该状态可能永久保留，前端持续轮询且用户无法重试。
4. `compile.py` 会再启动 `relate.py`。只终止父进程可能让子进程继续写共享 YAML，甚至在回滚后重新覆盖已恢复数据。
5. 当前没有启动恢复协议，无法区分已提交成功、执行中断、回滚中断、损坏快照或孤立 `compiling`。
6. 当前串行锁为 `threading.Lock`，仅约束单个 Python 进程；多 worker 会各自拥有独立锁并并发写同一批共享 YAML。
7. 上传会先落盘和摄入，再调度编译。事务准备失败或知识库正忙时，可能留下用户未明确接受的半成品文档。
8. 回滚、进程树终止或上传补偿在运行期间失败后，系统缺少统一的“不再提供不可信业务结果”门禁。

E005 将 E004 的“进程内临时失败回滚”升级为“单 API 实例、单全局活动事务、持久化快照、硬超时、进程树终止、启动恢复和严格就绪门禁”的事务化编译合同。

## 2. 目标

E005 必须实现：

1. 任何进入 `status=compiling` 的 API 来源任务，都必须拥有可持久化、可验证的事务清单和七类产物快照。
2. 同一时刻最多存在一个非终态编译事务；不再允许多个文档先进入 `compiling` 后排队。
3. 默认硬超时为 1800 秒；超时后终止完整进程树，确认无残留写进程后再回滚。
4. API 启动前同步恢复未提交事务；恢复全部成功后才开始监听请求。
5. 未提交但可能已经生成完整文件的任务仍按保守事务语义回滚，不把“看起来成功”解释为“已经提交”。
6. 回滚过程幂等；回滚中再次崩溃后，下一次启动可继续恢复。
7. 发现损坏清单、损坏快照、多个非终态事务、进程身份不可验证或孤立 `compiling` 时，严格阻止启动。
8. 运行期间出现无法证明一致性的故障时，进入 `recovery_required` 门禁，停止全部知识库业务接口。
9. 新增 `interrupted` 文档错误码，区分系统重启中断与模型、网络或文档处理失败。
10. 保持现有上传、重编译、删除和目录查询接口的主要成功响应形状，不向普通用户公开 job、PID、路径或技术诊断。
11. 提供只读检查和安全恢复 CLI；不提供绕过校验、强制删除证据或“带病启动”的旁路。
12. 自动化测试不调用真实 LLM、不访问公网、不污染真实 `originals/`、`raw/`、`wiki/`、`meta/` 数据。

## 3. 非目标

E005 不包含：

- 支持 `uvicorn --workers > 1`、Gunicorn 多 worker、多容器副本或多主机写入；
- Redis、数据库、分布式锁、分布式事务或持久化任务队列；
- 自动重新提交中断任务或自动重试 LLM 调用；
- 百分比进度、步骤心跳、任务取消、任务优先级或任务详情页面；
- 以心跳延长 30 分钟绝对硬超时；
- `repair` 模式、`start-api-anyway` 或其他绕过严格恢复的启动参数；
- 外部手工执行 `python -m scripts.compile` 与 API 事务之间的协调；
- 将所有共享 YAML 写入路径一次性改造成通用事务框架；
- 真实模型 Live UAT；
- 网络文件系统、磁盘损坏、存储控制器丢写或整机断电下的跨平台绝对持久性承诺。

## 4. 已确认的产品与架构决策

1. 采用持久化事务恢复，不采用仅把遗留 `compiling` 改为错误的轻量方案。
2. API 启动恢复采用严格阻断：不能安全恢复时不开始监听。
3. 默认硬超时为 `COMPILE_TIMEOUT_SECONDS=1800`，可配置但非法值阻止启动。
4. 超时终止采用“先尽力优雅终止整个进程树，等待 5 秒，再强制终止残留进程”。
5. 新增 `interrupted` 文档终态错误码。
6. E005 正式限定单 API 实例；第二个 worker 或实例必须启动失败。
7. 默认事务目录为 `.runtime/compile-transactions`，并要求生产部署使用持久化存储。
8. 发现 `compiling` 文档但没有匹配、可验证的事务时，阻止启动，不自动改状态。
9. Manifest 的原子 `COMMITTED` 状态是唯一提交点，不增加第二个 `commit.marker` 权威来源。
10. 不采用会削弱固定硬超时的任务心跳，也不采用允许不一致状态继续服务的 `repair` 启动模式。
11. 终态事务目录必须经过语义与哈希验证后才能清理，不能只依据状态字符串删除。
12. 恢复工具复用生产恢复库，不复制另一套实现。

## 5. 总体架构

E005 分为五个职责边界：

1. **配置与实例门禁**：解析并验证超时、终止宽限期和事务目录；获取跨平台 API 实例锁。
2. **事务存储**：创建 staging、快照、Manifest，执行耐久原子写，验证 schema、路径和哈希。
3. **编译执行器**：以独立进程组启动编译，记录进程身份，等待成功、失败或硬超时。
4. **恢复协调器**：终止遗留进程树、执行幂等回滚、验证恢复结果、写入文档终态、清理终态事务。
5. **API 与前端投影**：维持现有接口和轮询体验，仅公开稳定状态与安全错误码。

正常链路：

```text
请求
  -> 获取调度锁并确认无活动事务
  -> staging 中生成七项快照并验证
  -> 发布 PREPARED 事务
  -> 文档绑定 job 并进入 compiling
  -> Manifest 进入 SCHEDULED
  -> 后台任务获取执行锁
  -> 启动进程树并进入 RUNNING
  -> 成功语义验证
  -> Manifest 原子进入 COMMITTED
  -> 验证终态并清理事务目录
```

失败或超时链路：

```text
失败/超时/启动恢复
  -> Manifest 进入 ROLLBACKING
  -> 必要时终止并确认完整进程树退出
  -> 验证全部快照
  -> 恢复或删除七项产物
  -> 验证恢复结果
  -> 写入文档 error 终态并清除活动字段
  -> Manifest 进入 ROLLED_BACK
  -> 验证终态并清理事务目录
```

## 6. 部署模型与配置

### 6.1 单 API 实例合同

E005 运行模型为：

```text
一个 API 进程 + 一个全局活动编译事务
```

不支持多 worker。为避免只依赖部署文档或不可靠的 worker 环境变量，API 生命周期开始时必须获取跨平台操作系统级排他锁：

```text
.runtime/api-instance.lock
```

建议采用 `portalocker`，不自行实现 Windows 和 POSIX 两套文件锁。

锁生命周期：

```text
FastAPI lifespan 开始
  -> 获取 api-instance.lock
  -> 验证配置与事务目录
  -> 执行启动恢复
  -> 开始提供服务
  -> 整个 API 生命周期持续持锁
  -> lifespan 结束时释放
```

第二个进程获取失败时直接终止启动。异常退出后由操作系统释放实际锁；锁文件本身可以残留。

### 6.2 配置项

```text
COMPILE_TRANSACTION_DIR=.runtime/compile-transactions
COMPILE_TIMEOUT_SECONDS=1800
COMPILE_TERMINATION_GRACE_SECONDS=5
```

配置规则：

- 相对事务目录以仓库根目录解析；
- 超时允许范围建议为 60 至 86400 秒；
- 终止宽限期允许范围建议为 1 至 60 秒；
- 目录必须可创建、可写、可读取并支持同目录原子替换；
- 非法数值、不可写目录或原子替换探针失败时阻止启动；
- `.runtime/` 加入 `.gitignore`；
- `.env.example` 与部署文档明确说明生产环境必须将事务目录放入持久卷。

### 6.3 持久化存储要求

事务目录必须与 `raw/`、`wiki/`、`meta/` 具有同等级或更高的重启持久性。生产环境不得放在：

- 容器临时可写层；
- Kubernetes `emptyDir`；
- 系统临时目录；
- 自动清理的 cache 目录；
- 重启后不保留的 CI 或开发工作空间。

启动可验证目录的可写、原子替换和重新读取能力，但无法自动证明挂载介质重启后仍保留；持久卷配置属于部署硬合同。

## 7. 事务目录与 Manifest

### 7.1 目录结构

```text
.runtime/
├── api-instance.lock
└── compile-transactions/
    ├── .staging-{job_id}/
    └── {job_id}/
        ├── manifest.yaml
        ├── source-meta-before.yaml
        └── snapshots/
            ├── 00.bin
            ├── 01.bin
            └── ...
```

staging 目录仅用于事务准备；只有原子重命名为 `{job_id}` 后才成为正式事务。

### 7.2 快照白名单

每个事务固定覆盖以下七项：

```text
wiki/{doc_id}.summary.yaml
wiki/index.yaml
meta/ontology/{doc_id}.ontology.yaml
meta/ontology/global_ontology.yaml
meta/relations/{doc_id}.relations.yaml
meta/relations/knowledge_graph.yaml
meta/ontology/entity_relations.yaml
```

Manifest 中的路径仅用于比对和诊断。恢复目标必须由 `base_dir + doc_id + 固定白名单`重新计算，不得直接信任 Manifest 路径，以防路径越界或清单被篡改。

### 7.3 Manifest 示例

```yaml
schema_version: 1

job_id: 01J...
doc_id: doc_001
state: RUNNING

created_at: 2026-08-06T14:30:00+08:00
scheduled_at: 2026-08-06T14:30:01+08:00
started_at: 2026-08-06T14:30:02+08:00
deadline: 2026-08-06T15:00:02+08:00

timeout_seconds: 1800
termination_grace_seconds: 5
previous_document_status: compiled

process:
  pid: 12345
  create_time: 1786007401.25
  executable: C:/Python312/python.exe
  cwd: D:/.../port-knowledge-base
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

Manifest 不得保存 API Key、Authorization、完整环境变量、完整 LLM 请求、文档正文或未经脱敏的 stdout/stderr。

### 7.4 事务状态机

```text
PREPARED
    |
    v
SCHEDULED
    |
    v
RUNNING
   / \
  /   \
 v     v
COMMITTED    ROLLBACKING
                  |
                  v
             ROLLED_BACK
```

状态定义：

- `PREPARED`：正式事务目录、快照和 Manifest 已持久化，但文档尚未或刚开始绑定事务。
- `SCHEDULED`：文档已进入 `compiling` 并绑定 job，FastAPI 后台任务已登记。
- `RUNNING`：编译根进程已启动，进程身份和截止时间已持久化。
- `COMMITTED`：业务结果通过语义验证，Manifest 已越过唯一提交点。
- `ROLLBACKING`：正在执行或需要继续幂等回滚；任何恢复或业务终态写入未完成都必须停留在该状态。
- `ROLLED_BACK`：七项产物已恢复并验证，文档错误终态已写入，活动 job 字段已清除，可以清理事务目录。

不增加 `FINALIZED`。`ROLLED_BACK` 已包含文件恢复、结果验证和业务 meta 完成；再增加一层只会创造没有额外恢复价值的新崩溃窗口。

### 7.5 唯一提交点

Manifest 由 `RUNNING` 原子变为 `COMMITTED` 的时刻是唯一提交点。

- 提交点前崩溃：即使文件看起来已经完整生成，也一律回滚并标记 `interrupted`。
- 提交点后崩溃：启动恢复验证已提交业务终态，只清理残留事务目录，不回滚。

不增加独立 `commit.marker`，避免两个权威提交来源不一致。

## 8. 耐久写入合同

所有事务关键写入必须复用统一耐久写组件，包括：

- 快照二进制；
- `manifest.yaml`；
- `source-meta-before.yaml`；
- 文档活动字段和错误终态；
- 回滚恢复的目标文件。

标准步骤：

```text
同目录创建临时文件
  -> 写入完整内容
  -> flush
  -> fsync(临时文件)
  -> os.replace(临时文件, 目标文件)
  -> 重新读取或计算哈希验证
  -> POSIX 平台 fsync 父目录
```

Windows 和 POSIX 都执行文件级 `fsync` 与原子替换；POSIX 额外同步父目录。Windows 无法提供完全等价的目录同步时，不伪造跨平台断电保证。

E005 明确保障 Python/API 进程异常退出和正常操作系统重启后的恢复；不承诺覆盖硬件或文件系统损坏。

## 9. 锁与并发模型

### 9.1 锁职责

- `api-instance.lock`：进程生命周期锁，排除第二个 API 进程；不作为业务线程锁。
- `COMPILE_SCHEDULE_LOCK`：保护活动事务检查、事务准备、meta 绑定和后台任务登记。
- `COMPILE_EXECUTION_LOCK`：保护编译、进程终止、提交、回滚和删除产物操作。

不增加额外 transaction filesystem lock。API 和离线恢复 CLI 都必须先获取同一个实例锁；事务文件写入又处于调度或执行临界区内，额外文件锁没有新增安全收益，反而扩大死锁面。

### 9.2 线程锁顺序

唯一允许的线程锁顺序：

```text
COMPILE_SCHEDULE_LOCK -> COMPILE_EXECUTION_LOCK
```

规则：

- 调度路径只持调度锁；
- 执行路径只持执行锁；
- 删除路径持调度锁后，以非阻塞方式尝试执行锁；
- 持有执行锁时禁止反向获取调度锁；
- 启动恢复发生在开始接收请求之前，实例锁已持有，无需与业务线程竞争。

### 9.3 单全局活动事务

`PREPARED`、`SCHEDULED`、`RUNNING`、`ROLLBACKING` 均属于活动状态。只要存在一个活动事务：

- 其他文档重编译返回 `409 knowledge_base_busy`；
- 新文件上传在摄入前返回 `409 knowledge_base_busy`；
- 删除返回 `409 knowledge_base_busy`；
- 同一文档重复重编译优先返回 `409 compile_in_progress`。

不允许多个事务先后拍摄不同基线快照后排队，因为后一个事务回滚可能覆盖前一个事务已经提交的共享 YAML。

## 10. 事务准备与调度

### 10.1 重编译准备顺序

在 `COMPILE_SCHEDULE_LOCK` 内：

```text
确认 service_mode=ready
  -> 确认不存在活动事务
  -> 读取并保存 source meta
  -> 生成 job_id
  -> 在 .staging 中保存七项快照
  -> 记录存在性、大小和 SHA-256
  -> 验证全部快照
  -> 原子写 Manifest(state=PREPARED)
  -> 原子重命名 staging 为正式 job 目录
  -> 原子写文档 meta 为 compiling 并绑定 job
  -> 原子写 Manifest(state=SCHEDULED)
  -> BackgroundTasks.add_task(...)
  -> 请求成功返回
```

文档活动字段：

```yaml
status: compiling
compile_job_id: 01J...
compile_started_at: 2026-08-06T14:30:00+08:00
compile_deadline: 2026-08-06T15:00:00+08:00
```

没有持久化恢复依据的任务不得进入 `compiling`。

### 10.2 准备异常窗口

- staging 快照失败：删除 staging，文档状态不变。
- 正式 `PREPARED` 已发布但 meta 未绑定 job：启动恢复验证 meta 未变后清理事务，不写 `interrupted`。
- meta 已绑定 job 但 Manifest 尚为 `PREPARED`：按中断事务回滚。
- `BackgroundTasks.add_task()` 失败：请求线程内立即进入安全回滚；成功前不得返回接受响应。
- 未正式发布的 `.staging-*` 不属于事务，只能在确认没有文档绑定该 job 后清理。

### 10.3 Orphan staging 规则

启动时在实例锁保护下扫描 `.staging-*`：

- staging 不包含正式发布的可解析 Manifest；
- 没有任何 meta 的 `compile_job_id` 指向该 job；
- API 尚未开始接收请求。

满足以上条件时可以直接删除，不需要时间宽限。若 staging 与文档绑定关系矛盾，则阻止启动，不猜测事务是否已接受。

## 11. 上传事务与补偿

上传接口必须把“全局忙预检、摄入、事务准备和任务登记”纳入同一调度锁保护：

```text
获取调度锁
  -> 确认无活动事务且服务 ready
  -> 暂存并哈希去重
  -> ingest 生成 doc_id、original、raw txt 和 raw meta
  -> 准备持久化编译事务
  -> 绑定 meta 并登记后台任务
  -> 释放锁并返回 processing
```

### 11.1 全局忙

在写入最终 `originals/` 和 `raw/` 之前返回：

```http
409 Conflict
{"detail":{"code":"knowledge_base_busy"}}
```

不得创建 doc_id 或残留临时文件。

### 11.2 摄入后事务准备失败

若本请求已经新建摄入文件，但任务未被接受：

- 只删除本请求明确记录的新 original、raw txt 和 raw meta；
- 不调用通用 `remove_doc()`；
- 不触碰既有索引、图谱或其他文档；
- 补偿成功后返回 `503 compile_transaction_unavailable`。

补偿失败时：

- 进入运行时 `recovery_required` 门禁；
- 保留诊断和新建文件证据；
- 当前进程不再提供任何知识库业务接口。

### 11.3 重复文件

现有 `skipped=true` 合同保持不变，不创建事务。

## 12. 编译执行与进程身份

### 12.1 执行方式

E005 将无超时 `subprocess.run()` 改为受控 `subprocess.Popen()`：

```text
sys.executable -m scripts.compile <doc_id>
```

要求：

- `cwd=BASE_DIR`；
- 继承父进程环境并设置 `PYTHONUTF8=1`；
- 捕获 stdout/stderr，但只保留限长、脱敏诊断；
- POSIX 创建独立 session/process group；
- Windows 使用 `CREATE_NEW_PROCESS_GROUP`；
- 启动后立即记录进程身份，再将 Manifest 原子写为 `RUNNING`。

### 12.2 进程身份

Manifest 至少记录：

- PID；
- 创建时间；
- 可执行文件；
- cwd；
- 规范化业务命令指纹；
- 进程组标识；
- 平台。

命令指纹只反映业务意图，例如：

```text
scripts.compile|doc_001
```

不把 Python 解释器绝对路径混入指纹，以避免跨平台和虚拟环境差异造成误判；解释器路径仍作为独立身份字段校验。

恢复或超时终止前：

1. PID 不存在：视为原进程已退出，可继续恢复。
2. PID、创建时间、可执行文件、cwd 和可读取的业务命令身份全部匹配：允许终止。
3. PID 存在但身份不匹配或关键身份无法验证：不误杀，不修改共享数据，阻止启动或进入 `recovery_required`。

建议采用 `psutil` 枚举后代、读取创建时间并执行跨平台终止。

## 13. 硬超时与进程树终止

### 13.1 超时语义

超时从任务进入 `RUNNING` 后的 `started_at` 计算，默认 1800 秒。它是绝对上限，不因日志输出、LLM 重试、步骤变化或心跳延长。

达到截止时间：

```text
Manifest -> ROLLBACKING
failure.original_code -> timeout
```

随后先终止进程树，再回滚。

### 13.2 POSIX

```text
SIGTERM 发送给整个进程组
  -> 等待 termination_grace_seconds
  -> SIGKILL 发送给仍存活的进程组
  -> 验证根进程和全部后代已退出
```

### 13.3 Windows

```text
尝试向独立进程组发送 CTRL_BREAK_EVENT
  -> 等待 termination_grace_seconds
  -> 枚举已验证身份的后代
  -> 终止后代，再终止根进程
  -> 强制 kill 残留进程
  -> 验证根进程和全部后代已退出
```

Windows 的合作式退出属于尽力而为；无法投递控制事件时，不能宣称优雅终止，等待期后进入强制阶段。

### 13.4 回滚前置条件

只有确认根进程和全部后代均不再存活后，才允许恢复快照。无法确认时：

- 不执行回滚；
- Manifest 保持 `ROLLBACKING`；
- 记录安全诊断；
- 当前实例进入 `recovery_required`；
- 下一次启动或离线恢复继续处理。

## 14. 成功提交合同

子进程退出后，成功必须同时满足：

1. 根进程及全部后代已退出；
2. 返回码为 0；
3. `raw/{doc_id}.meta.yaml` 明确为 `compiled`；
4. 必需单文档产物存在并可解析；
5. index 中恰有一条目标文档记录；
6. 共享 YAML 若存在，顶层结构合法；
7. wrapper 原子规范化 meta，清除活动 job 和旧错误字段；
8. Manifest 原子写为 `COMMITTED`。

### 14.1 必须存在并可解析

- `wiki/{doc_id}.summary.yaml`；
- `meta/ontology/{doc_id}.ontology.yaml`；
- `wiki/index.yaml`；
- `raw/{doc_id}.meta.yaml`。

### 14.2 语义验证

- summary 的 `doc_id` 匹配；
- ontology 的 `doc_id` 匹配；
- index 中目标文档记录唯一；
- meta 状态为 `compiled`；
- doc 关系文件若存在，其 `doc_id` 匹配；
- `global_ontology.yaml`、`knowledge_graph.yaml`、`entity_relations.yaml` 若存在，必须可解析且顶层结构合法。

七项是快照覆盖范围，不代表成功后每项都必须新建或改变哈希。无新本体节点、无关系或无实体边均可能是合法成功结果。

## 15. 回滚合同

### 15.1 回滚顺序

```text
写 Manifest=ROLLBACKING 和原始失败原因
  -> 确认无遗留写进程
  -> 验证全部快照大小与 SHA-256
  -> 原本存在的文件逐字节原子恢复
  -> 原本不存在的文件 unlink(missing_ok=True)
  -> 再次验证所有目标与事务前状态一致
  -> 写文档 error 终态
  -> 清除 compile_job_id、compile_started_at、compile_deadline
  -> 写 Manifest=ROLLED_BACK
  -> 终态验证并清理事务目录
```

### 15.2 幂等性

如果在 `ROLLBACKING` 中再次崩溃：

- 已恢复文件再次写入相同字节；
- 原本不存在的文件再次删除；
- meta 再次写入相同终态；
- 验证通过后进入 `ROLLED_BACK`。

不得通过“已经改过一部分”跳过剩余校验。

### 15.3 回滚失败语义

任何快照损坏、目标写入失败、恢复后哈希不一致或业务 meta 写入失败，都不得进入 `ROLLED_BACK`。事务保持 `ROLLBACKING`，服务不就绪。

实现可对原始 meta 做最佳努力的 `rollback_failed` 诊断写入，但该字段不代表已经恢复完成。下一次恢复成功后，最终文档错误码应回到事务保存的原始失败原因，例如 `timeout`、`interrupted` 或 `compile_failed`；恢复失败历史保留在日志和 Manifest 的 recovery 诊断中。

这样避免“旧版本已经成功恢复，前端却永久显示恢复异常”的矛盾。

## 16. 启动恢复

FastAPI 开始监听前，在实例锁保护下按以下顺序执行：

```text
验证配置和事务目录
  -> 清理可证明未发布的 staging
  -> 扫描正式事务目录
  -> 验证非终态事务数量
  -> 处理每个事务状态
  -> 扫描全部 raw meta
  -> 确认不存在孤立 compiling
  -> service_mode=ready
  -> 开始监听
```

### 16.1 非终态事务数量

正常合同最多一个非终态事务。发现两个及以上 `PREPARED`、`SCHEDULED`、`RUNNING` 或 `ROLLBACKING`：

- 不猜测快照先后关系；
- 不自动依次回滚；
- 直接阻止启动。

多个共享 YAML 快照可能基于不同基线，错误顺序会覆盖已成功数据。

### 16.2 各状态处理

| 状态 | 启动处理 |
|---|---|
| `PREPARED` 且 meta 未绑定 job | 验证 meta 未变，清理事务，不写 `interrupted` |
| `PREPARED` 且 meta 已绑定 job | 回滚并写 `interrupted` |
| `SCHEDULED` | 回滚并写 `interrupted` |
| `RUNNING` 且进程不存在 | 回滚并写 `interrupted` |
| `RUNNING` 且身份匹配 | 终止完整进程树后回滚并写 `interrupted` |
| `RUNNING` 且身份不可验证 | 阻止启动，不误杀 |
| `ROLLBACKING` | 依据保存的原始原因继续幂等回滚 |
| `COMMITTED` | 验证已提交业务终态后只清理目录 |
| `ROLLED_BACK` | 验证恢复和错误终态后只清理目录 |
| 未知 schema 或未知状态 | 阻止启动 |

### 16.3 孤立 compiling

事务处理结束后扫描 `raw/*.meta.yaml`。发现：

```yaml
status: compiling
```

但不存在匹配、可验证的事务时：

- 输出全部相关 doc_id；
- 不自动修改状态；
- 不假定共享产物可信；
- 阻止 API 启动。

## 17. 错误码与元数据合同

### 17.1 文档终态错误码

写入 `status=error`：

| 错误码 | 含义 | 旧版本状态 |
|---|---|---|
| `llm_configuration` | 模型配置、依赖或鉴权失败 | 已恢复 |
| `service_unavailable` | 上游服务或网络不可用 | 已恢复 |
| `timeout` | 超过硬超时 | 已恢复 |
| `document_processing` | 文档内容无法处理 | 已恢复 |
| `compile_failed` | 其他编译失败 | 已恢复 |
| `interrupted` | 服务重启发现未提交事务 | 已恢复 |
| `rollback_failed` | 当前恢复尝试未完成的诊断状态 | 未证明恢复；服务不就绪 |

`interrupted` 固定安全文案：

```text
编译任务因服务重启中断，旧版本已恢复，请重新编译
```

### 17.2 请求级错误码

仅出现在 HTTP `detail.code`，不写入文档终态：

| 错误码 | HTTP | 场景 |
|---|---:|---|
| `compile_in_progress` | 409 | 同一文档已有活动事务 |
| `knowledge_base_busy` | 409 | 其他文档事务正在进行 |
| `compile_transaction_unavailable` | 503 | 事务准备或接受失败，任务未被接受 |
| `recovery_required` | 503 | 当前实例处于安全门禁 |

启动阶段非法配置、锁冲突、损坏事务和孤立 `compiling` 不产生 HTTP 响应，因为 API 不开始监听。

### 17.3 活动字段和终态清理

活动期间原始 meta 可包含：

```yaml
compile_job_id: 01J...
compile_started_at: ...
compile_deadline: ...
```

进入 `compiled` 或已验证恢复后的 `error` 时，必须删除这些活动字段。

`error_message` 继续经过脱敏、单行化和 500 字符限长，只保存在原始 meta。公共目录不得投影：

- `compile_job_id`；
- PID、命令和事务路径；
- `error_message`；
- 快照、失败路径和恢复诊断。

## 18. API 合同

### 18.1 重编译

接口保持：

```http
POST /api/v1/docs/{doc_id}/recompile
```

成功响应保持：

```json
{"status":"recompiling","doc_id":"doc_001"}
```

失败：

- 文档不存在：404；
- 同文档活动：409 `compile_in_progress`；
- 其他事务活动：409 `knowledge_base_busy`；
- 事务准备失败：503 `compile_transaction_unavailable`；
- 运行门禁：503 `recovery_required`。

在事务正式发布、meta 绑定和后台任务登记全部完成前，不能返回成功。

### 18.2 上传与摄入

现有成功响应形状保持：

```json
{
  "status": "processing",
  "skipped": false,
  "doc_id": "doc_001",
  "filename": "example.pdf",
  "message": "摄入成功，后台自动编译中..."
}
```

全局忙必须在最终落盘和摄入前返回 409。摄入后事务准备失败必须执行第 11 节补偿清理。

重复文件 `skipped=true` 合同不变。

### 18.3 删除

接口保持：

```http
DELETE /api/v1/docs/{doc_id}
```

- 任一活动事务：409 `knowledge_base_busy`；
- 目标文档活动：409 `compile_in_progress`；
- 服务门禁：503 `recovery_required`；
- 文档不存在：404；
- 成功响应形状不变。

### 18.4 运行时安全门禁

应用内部状态：

```text
service_mode = ready | recovery_required
```

启动恢复失败时 API 不启动。运行期间若出现无法终止进程树、快照损坏、回滚失败、恢复哈希不一致或上传补偿失败：

- 不自动恢复为 ready；
- 所有知识库业务接口返回 503 `recovery_required`；
- 只允许 `/api/v1/health` 与 `/api/v1/ready`；
- 保留事务和诊断证据；
- 由重启严格恢复或离线 CLI 处理。

读接口也必须阻断，因为索引、本体和图谱可能处于无法证明一致的状态。

## 19. 健康与就绪检查

### 19.1 Liveness

保留：

```http
GET /api/v1/health
```

始终快速返回 HTTP 200，不调用 LLM。增加安全字段：

```json
{
  "status": "ok",
  "ready": false,
  "recovery_state": "recovery_required",
  "version": "2.0.0"
}
```

不得暴露 job_id、PID、路径或内部异常。

### 19.2 Readiness

新增：

```http
GET /api/v1/ready
```

正常：

```http
200 OK
{"status":"ready"}
```

门禁：

```http
503 Service Unavailable
{"status":"not_ready","code":"recovery_required"}
```

部署平台以 `/ready` 决定是否发送业务流量，以 `/health` 判断进程是否存活。

## 20. 前端合同

### 20.1 类型拆分

建议将当前统一错误码类型拆成：

```typescript
type DocumentCompileErrorCode =
  | 'llm_configuration'
  | 'service_unavailable'
  | 'timeout'
  | 'document_processing'
  | 'compile_failed'
  | 'interrupted'
  | 'rollback_failed';

type CompileRequestErrorCode =
  | 'compile_in_progress'
  | 'knowledge_base_busy'
  | 'compile_transaction_unavailable'
  | 'recovery_required';
```

`DocMeta.error_code` 只能使用文档终态类型；`ApiError.code` 可使用请求错误类型。

### 20.2 固定文案

```text
interrupted:
编译任务因服务重启中断，旧版本已恢复，请重新编译

compile_transaction_unavailable:
编译任务暂时无法创建，请稍后重试或联系管理员

recovery_required:
知识库正在恢复或需要管理员处理，暂不可用
```

任何文案不得包含 job、PID、绝对路径、异常类型、原始 stderr/stdout、密钥或环境变量值。

### 20.3 轮询与提示

保持 E004 现有合同：

- 存在 `raw/compiling` 时每 3 秒轮询；
- 全部进入终态后停止；
- 首轮历史错误不弹 Toast；
- 本轮 `compiling -> error` 只弹一次；
- 新一轮重编译可以再次提示；
- 瞬时轮询失败保留上一份目录；
- 旧纪元和乱序响应不能覆盖新状态。

API 重启后恢复上线，`compiling -> error/interrupted` 沿用现有迁移检测显示一次 Toast 和卡片内固定文案。首次打开页面时已是 `interrupted`，视为历史错误，不弹 Toast。

不新增事务详情、倒计时、恢复进度、自动重试或管理员操作页面。

## 21. 终态事务清理

终态目录不能只依据 state 删除。

### 21.1 COMMITTED

清理前必须验证：

- Manifest schema、job 和 doc 匹配；
- meta 为 `compiled`；
- meta 已清除活动 job 字段；
- 必需产物通过第 14 节语义验证；
- 不存在指向该 job 的 `compiling` 文档。

不要求产物等于事务前哈希，因为成功提交本来会改变文件。

### 21.2 ROLLED_BACK

清理前必须验证：

- 原本存在的每项产物与事务前 SHA-256 一致；
- 原本不存在的每项产物仍不存在；
- meta 为 `error`；
- error_code 与事务原始失败原因一致；
- 活动 job 字段已清除。

### 21.3 删除失败

若仅目录删除失败，但终态验证成功：

- 记录 warning；
- 不把它解释为活动事务；
- 不回滚已提交结果；
- 启动恢复和 CLI 后续继续清理。

本阶段不增加保留天数。终态事务应尽快清理，避免长期保存业务快照。

## 22. 离线恢复工具

新增：

```text
scripts/compile_recovery.py
```

工具复用生产恢复库，并在任何修改前获取同一个 `api-instance.lock`。获取失败说明 API 正在运行，拒绝执行。

支持：

```text
python -m scripts.compile_recovery inspect
python -m scripts.compile_recovery verify <job_id>
python -m scripts.compile_recovery recover <job_id>
python -m scripts.compile_recovery cleanup-terminal
```

### 22.1 inspect

只读输出事务、状态、schema、快照完整性、文档绑定、进程身份、孤立 `compiling` 和阻断原因。

### 22.2 verify

只读验证指定事务的 schema、状态、固定路径、快照哈希、meta 绑定和进程身份。

### 22.3 recover

只允许处理 `PREPARED`、`SCHEDULED`、`RUNNING`、`ROLLBACKING`，执行与启动恢复相同的安全流程。

### 22.4 cleanup-terminal

只清理通过第 21 节完整验证的 `COMMITTED` 和 `ROLLED_BACK`。

明确不提供：

```text
force-delete
ignore-checksum
skip-process-check
mark-resolved
start-api-anyway
```

## 23. 测试策略

### 23.1 事务存储单元测试

| 编号 | 场景 | 核心断言 |
|---|---|---|
| T01 | staging 创建七项快照 | 固定白名单完整 |
| T02 | 快照持久化 | 大小和 SHA-256 正确 |
| T03 | Manifest 原子发布 | 无半成品正式目录 |
| T04 | 非法 schema | 恢复拒绝 |
| T05 | 未知 state | 恢复拒绝 |
| T06 | 路径越界 | 不访问白名单外文件 |
| T07 | 快照损坏 | 不执行目标写入 |
| T08 | 原目标不存在 | 回滚后仍不存在 |
| T09 | 原目标存在 | 逐字节恢复 |
| T10 | Manifest 写失败 | meta 不进入 compiling |
| T11 | staging 无绑定 | 启动安全清理 |
| T12 | staging 与 meta 矛盾 | 启动阻断 |

### 23.2 状态机测试

| 编号 | 起始状态 | 预期 |
|---|---|---|
| S01 | PREPARED、meta 未绑定 | 清理，不写 interrupted |
| S02 | PREPARED、meta 已绑定 | 回滚并写 interrupted |
| S03 | SCHEDULED | 回滚并写 interrupted |
| S04 | RUNNING、进程不存在 | 直接回滚 |
| S05 | RUNNING、身份匹配 | 终止树后回滚 |
| S06 | RUNNING、PID 身份不匹配 | 不误杀，阻断 |
| S07 | ROLLBACKING | 幂等继续 |
| S08 | COMMITTED 验证成功 | 只清理 |
| S09 | COMMITTED 验证失败 | 不清理，阻断 |
| S10 | ROLLED_BACK 验证成功 | 只清理 |
| S11 | 多个非终态事务 | 阻断启动 |
| S12 | 孤立 compiling | 阻断启动 |

### 23.3 超时与进程树测试

| 编号 | 场景 | 核心断言 |
|---|---|---|
| P01 | 截止前完成 | 正常提交 |
| P02 | 达到硬超时 | terminate 后 kill |
| P03 | 根进程派生子孙进程 | 全树退出后才回滚 |
| P04 | 终止后仍有残留 | 不回滚，进入门禁 |
| P05 | PID 复用 | 不终止无关进程 |
| P06 | 命令身份不匹配 | 不终止 |
| P07 | 超时恢复失败 | 保持 ROLLBACKING、不就绪 |

跨平台要求：

- Windows 本地验证真实进程树；
- CI POSIX 环境验证真实进程组；
- 单元测试可 mock 系统调用，但每个平台至少需要一个真实短生命周期进程测试；
- 不启动真实 LLM 或真实 `relate.py` 业务调用。

### 23.4 API 合同测试

| 编号 | 场景 | 核心断言 |
|---|---|---|
| A01 | 重编译接受 | 成功形状兼容 |
| A02 | 同文档活动 | 409 compile_in_progress |
| A03 | 其他文档活动 | 409 knowledge_base_busy |
| A04 | 快照准备失败 | 503，原 meta 和产物不变 |
| A05 | 上传全局忙 | 摄入前 409，数据目录不变 |
| A06 | 摄入后事务准备失败 | 只删本请求新建文件 |
| A07 | 上传补偿失败 | recovery_required |
| A08 | 运行门禁 | 业务接口全部 503 |
| A09 | health 门禁状态 | 200、ready=false |
| A10 | ready 门禁状态 | 503 |
| A11 | 公共目录 | 不暴露 job、PID、error_message |
| A12 | 终态写入 | 清除活动字段 |
| A13 | 删除与事务互斥 | 固定 409 安全文案 |

### 23.5 前端测试

| 编号 | 场景 | 核心断言 |
|---|---|---|
| F01 | 历史 interrupted | 卡片显示，不弹 Toast |
| F02 | compiling -> interrupted | 每轮一次 Toast |
| F03 | 中断后重试再失败 | 新一轮可再次 Toast |
| F04 | compile_transaction_unavailable | 固定文案 |
| F05 | recovery_required | 固定文案 |
| F06 | 轮询瞬时断线 | 保留上一份快照 |
| F07 | 恢复后新快照 | 旧请求不能覆盖 |
| F08 | 全部用户文案 | 不含内部技术信息 |

扩展现有轮询与过期响应测试，不另建第二套页面状态机。

### 23.6 崩溃恢复集成测试

- **R1 SCHEDULED 崩溃**：绑定完成、子进程未启动，重启后回滚并写 `interrupted`。
- **R2 RUNNING 崩溃**：子进程修改部分产物，重启识别并终止遗留树，逐字节恢复。
- **R3 提交点前崩溃**：子进程成功但 Manifest 仍为 `RUNNING`，重启按未提交回滚。
- **R4 提交点后崩溃**：Manifest 已 `COMMITTED`、清理前退出，重启只验证和清理。
- **R5 回滚中再次崩溃**：`ROLLBACKING` 中只恢复部分文件，第二次启动幂等完成。
- **R6 损坏恢复依据**：快照损坏，启动严格阻断且不修改业务文件。
- **R7 双崩溃恢复**：`RUNNING -> 崩溃 -> 启动恢复进入 ROLLBACKING -> 再次崩溃 -> 再次启动 -> 完整恢复 -> interrupted -> 清理`。

破坏性崩溃测试必须运行在临时仓库副本或 `tmp_path`，不得以真实知识库数据作为 kill、超时或恢复目标。

## 24. 依赖与代码边界预期

实施预计涉及但不限于：

- `api/compile_jobs.py`：拆分事务存储、执行、终止与恢复职责，或将新职责下沉到独立模块；
- `api/main.py`：lifespan、实例锁、启动恢复、服务门禁、调度和 API 合同；
- `scripts/doc_admin.py`：活动字段和终态原子写入合同；
- `scripts/compile_recovery.py`：离线检查与恢复入口；
- `frontend/src/lib/api.ts`：错误码类型拆分和固定文案；
- 现有 Wiki 页面及测试：只做 E005 必要扩展；
- `.gitignore`、`.env.example`、依赖清单和部署文档；
- 后端、前端及真实进程树专项测试。

建议新增运行依赖：

- `portalocker`：跨平台 API 实例排他锁；
- `psutil`：进程身份、后代枚举和跨平台终止。

实施计划必须先核对仓库当前依赖管理方式、Python 版本和 CI 平台，再锁定兼容版本；本设计不预先指定具体版本号。

不得在 E005 顺手统一 `api/main.py` 与 `app/` 两套后端，也不得重构与事务恢复无关的检索、问答、本体或关系算法。

## 25. 可观测性与安全日志

结构化日志至少覆盖：

- job 创建、状态迁移和终态；
- 启动恢复发现的事务数量与状态；
- 超时、终止阶段和残留进程数量；
- 快照或恢复验证失败的仓库相对路径；
- service_mode 进入 `recovery_required` 的原因类别；
- 终态事务清理失败。

日志不得包含：

- API Key、Authorization 或完整环境变量；
- 文档正文；
- 完整 LLM 请求或响应；
- 未脱敏 stdout/stderr；
- 用户可控制的绝对路径拼接结果。

错误消息继续复用 E004 脱敏和 500 字符限制。Manifest 只保存稳定、最小的恢复信息。

## 26. 数据与离线安全门禁

所有自动化验证必须：

- 显式清空真实模型 Key；
- 使用黑洞代理或网络拦截；
- 不调用真实 LLM；
- 不运行真实数据上的破坏性恢复；
- 对真实 `originals/`、`raw/`、`wiki/`、`meta/` 执行测试前后 path、size、SHA-256 和 `mtime_ns` 清单比对；
- 清除测试生成的 `.runtime/` 临时产物；
- 保持完整后端 pytest、前端 Jest、ESLint、Next.js build 和三个 required checks 绿色；
- 不更改 required check 名称：`repository-integrity`、`python-core`、`frontend-unit-build`。

## 27. 迁移与兼容性

E005 上线后的首次启动执行严格扫描：

- 没有事务且没有 `compiling`：正常启动。
- E004 遗留 `compiling` 但没有 E005 Manifest：阻止启动并输出 doc_id；不自动改状态。
- 旧 `error`、`compiled`、`raw` meta 不要求补充 job 字段。
- 公共 API 成功形状保持兼容；新增字段仅出现在 health/ready，新增错误码只扩展失败合同。
- 前端继续识别原状态集合，不新增可见状态值；`interrupted` 仍表现为 `status=error`。

部署前必须检查是否存在遗留 `compiling`。若存在，由离线检查工具或明确的人工数据审计任务处理，不能在部署脚本中直接改 YAML 或删除事务证据。

## 28. 完成标准

E005 只有同时满足以下条件，才可提交代码审查：

1. 单 API 实例锁在 Windows 和 POSIX 验证有效。
2. 没有持久化事务依据的任务无法进入 `compiling`。
3. 任何时刻最多一个全局活动事务。
4. 30 分钟默认硬超时与可配置边界测试通过。
5. 超时和启动恢复都能终止完整进程树；身份不确定时不误杀。
6. 提交点前崩溃回滚，提交点后崩溃只清理。
7. `ROLLBACKING` 二次崩溃后可幂等恢复。
8. 损坏快照、未知 schema、多活动事务和孤立 `compiling` 均严格阻止启动。
9. 上传事务准备失败不会留下半接受文档；补偿失败会触发门禁。
10. `interrupted`、请求级 409/503 和前端固定文案符合合同。
11. 公共 API 不暴露 job、PID、路径、快照或原始技术错误。
12. `/health` 与 `/ready` 的 liveness/readiness 语义清晰。
13. 离线恢复 CLI 没有绕过安全检查的命令。
14. 真实知识库数据清单测试前后完全一致。
15. 完整后端、前端、lint、build 和 CI 门禁全部通过。
16. 变更范围仅包含 E005 必需代码、测试、依赖和文档，不夹带后端统一或通用 YAML 重构。

## 29. 设计自审结论

本规格已完成以下自审：

- **占位符检查**：无 `TBD`、`TODO` 或待定产品决策。
- **一致性检查**：固定 30 分钟硬超时、严格启动阻断、单实例、单活动事务、唯一 Manifest 提交点和不自动重试相互一致。
- **状态机检查**：`ROLLED_BACK` 明确定义为文件恢复、验证和业务终态全部完成；不需要冗余 `FINALIZED`。
- **提交窗口检查**：提交点前一律回滚，提交点后只清理，没有依赖“文件看起来成功”的模糊判断。
- **回滚失败检查**：未完成恢复时保持 `ROLLBACKING` 和不就绪；后续成功恢复后使用原始业务失败原因，避免永久显示过时的恢复异常。
- **并发检查**：实例锁排除第二进程，线程锁只允许调度锁到执行锁，且最多一个活动事务。
- **范围检查**：没有引入多 worker、分布式锁、数据库、自动重试、心跳延时、repair 模式或通用 YAML 重构。
- **可测试性检查**：关键崩溃窗口、进程树、双崩溃、损坏快照、上传补偿和前端错误迁移都有明确测试入口。

设计内容已具备进入实施计划的条件，但必须先由用户审阅并批准本书面规格。
