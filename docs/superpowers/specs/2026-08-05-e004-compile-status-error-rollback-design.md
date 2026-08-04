# E004 后台编译状态、错误回滚与前端友好提示闭环设计

- **任务编号**：E004
- **设计状态**：已确认，待实施计划
- **基线分支**：`dev`
- **基线提交**：`a18a8baac2b54d055758cadb699510a41f96249e`
- **设计分支**：`docs/e004-compile-status-design`
- **日期**：2026-08-05
- **范围属性**：设计文档；本提交不修改业务代码

## 1. 背景与问题

当前上传和重编译使用两条不一致的后台编译路径：

1. 上传路径通过 `compile_ingested_task()` 启动 `python -m scripts.compile <doc_id>`，但不捕获输出、不检查返回码；缺少编译脚本时直接返回。
2. 重编译路径在 API 内部直接初始化 LLM 并调用 `compile_doc()`，所有异常被捕获后静默丢弃。
3. `scripts.compile.main()` 在初始化 LLM 客户端后才进入 `compile_doc()`。若依赖、密钥或客户端初始化失败，文档元数据不会进入可靠终态。
4. `compile_doc()` 单篇失败时返回 `False`，但 CLI 主函数仍可能以进程返回码 0 结束，因此不能仅依靠子进程返回码判断成功。
5. 编译会在最终写入 `status=compiled` 前依次覆盖单文档摘要、本体、全局索引及可能的关系产物。若中途失败，旧版本可能被部分覆盖，新文档也可能留下半成品。
6. 前端知识库页面已有条件轮询和状态徽标，但轮询间隔为 10 秒；重编译和上传即时失败会直接展示原始 `Error.message`，可能包含 HTTP 状态、异常类型、环境变量名或其他技术信息。

E004 将上传和重编译统一为同一后台任务合同，使 API 发起的编译在当前单 API 进程内形成可验证的状态、错误、回滚和用户提示闭环。

## 2. 目标

E004 必须实现：

1. 上传摄入或重编译请求被接受后，接口返回前立即写入 `status=compiling`。
2. 上传和重编译只调度同一个后台编译入口。
3. 同一文档处于 `compiling` 时拒绝重复重编译，不启动第二任务。
4. 后台任务最终落入 `compiled` 或 `error`，不静默吞掉已检测到的启动、配置或执行失败。
5. 首次编译失败不污染检索索引；重编译失败后恢复上一次成功产物。
6. 技术错误在后端脱敏、压缩并限长保存；前端只显示固定、友好的中文提示。
7. 有待处理文档时，知识库页面每 3 秒刷新状态；全部进入终态后停止轮询。
8. 轮询检测到本轮 `raw/compiling -> error` 时只弹出一次提示，并在文档卡片内持续显示友好原因。
9. 自动化测试不调用真实 LLM、不启动真实编译进程、不修改仓库真实数据。

## 3. 非目标

E004 不包含：

- 数据库或持久化任务表；
- Redis、Celery 或其他任务队列；
- 新的任务查询 API；
- SSE、WebSocket 或后台推送；
- 进度百分比、任务取消、优先级或重排；
- 前端技术详情入口；
- 多 API 进程、多机器或外部 CLI 之间的分布式互斥；
- API 进程被强制终止后的自动恢复；
- 后台子进程的硬看门狗、跨平台进程树终止；
- 全部共享 YAML 写入的通用原子化重构；
- 将关系检测子进程的 warning 升级为整个文档编译失败；
- Live UAT 或真实模型调用。

## 4. 已确认的产品决策

1. `error_message` 保存经过脱敏、单行化和长度限制的具体技术原因，最多 500 个字符。
2. 接口接受任务后立即写入 `compiling`；“已排队”和“正在执行”统一显示为“编译中”。
3. 重编译失败时保留并恢复上一次成功产物，旧版本仍可用于搜索、问答和查看。
4. 已为 `compiling` 的文档再次重编译时返回 `409 Conflict`，不重复调度。
5. 前端按稳定错误类别显示不同的友好提示，不展示技术详情。
6. 存在待处理文档时每 3 秒轮询；全部进入终态后自动停止。
7. 所有 API 来源编译在当前单 API 进程内全局串行，避免共享索引和全局本体相互覆盖。
8. 所有编译——包括首次上传——都建立产物快照；失败时回滚。

## 5. 状态与数据合同

### 5.1 状态机

```text
raw      -> compiling -> compiled
                       -> error
error    -> compiling -> compiled | error
compiled -> compiling -> compiled | error
```

规则：

- 上传摄入成功后，在加入后台任务前写入 `compiling` 并清除旧错误字段。
- 重编译端点在调度临界区内读取元数据；若当前状态为 `compiling`，返回冲突；否则写入 `compiling` 并清除旧错误字段。
- 后台成功仅以最终元数据明确为 `compiled` 为准。
- 后台失败写入 `status=error`、稳定 `error_code` 和脱敏 `error_message`。
- `raw/{doc_id}.meta.yaml` 是管理状态的权威来源；`wiki/index.yaml` 继续是检索候选的权威来源。
- E004 新增或修改的元数据状态写入使用同目录临时文件加 `os.replace()`，避免状态文件部分写入。
- 重编译失败后，原索引条目和旧摘要恢复，因此文档虽然管理状态为 `error`，仍可由检索链路使用上一次成功版本。

### 5.2 元数据字段

```yaml
status: error
error_code: compile_failed
error_message: 编译失败的脱敏技术原因
```

成功时：

- 写入 `status: compiled`；
- 删除或置空 `error_code`；
- 删除或置空 `error_message`。

进入新一轮 `compiling` 时，同样清除上一轮错误字段。

### 5.3 目录投影

`_load_document_catalog()` 增加 `error_code` 投影，使 `/api/v1/docs`、`/api/v1/docs/{doc_id}` 和 `/api/v1/wiki/index` 可返回稳定机器错误类别。现有 `error_message` 仍保存在元数据中；前端类型和组件不得读取或渲染该技术字段。

## 6. API 合同

### 6.1 上传与摄入

`POST /api/v1/upload` 和 `POST /api/v1/ingest` 保持现有成功响应形状：

```json
{
  "status": "processing",
  "skipped": false,
  "doc_id": "doc_xxx",
  "filename": "example.pdf",
  "message": "摄入成功，后台自动编译中..."
}
```

在该响应返回前：

1. 文件已完成落盘、去重和同步摄入；
2. 对应元数据已写为 `compiling`；
3. 统一后台任务已加入 `BackgroundTasks`。

重复文件仍保持 `skipped=true` 合同，不创建编译任务。

### 6.2 重编译

`POST /api/v1/docs/{doc_id}/recompile` 成功：

```json
{
  "status": "recompiling",
  "doc_id": "doc_xxx"
}
```

元数据不存在时保持 `404`。

文档已处于 `compiling` 时返回：

```http
HTTP/1.1 409 Conflict
```

```json
{
  "detail": {
    "code": "compile_in_progress"
  }
}
```

响应体不包含子进程输出、堆栈、密钥、环境变量值或完整技术异常。

## 7. 后台编译架构

### 7.1 唯一入口

上传和重编译统一调度：

```python
run_compile_task(doc_id: str) -> None
```

该函数负责：

1. 获取全局执行锁；
2. 建立编译产物快照；
3. 启动隔离子进程；
4. 捕获 stdout/stderr，但每个流最多取末尾 8192 个字符送入错误分类；完整输出不写入元数据；
5. 检查启动异常、返回码和最终元数据；
6. 成功时清理快照和错误字段；
7. 失败时恢复快照并写入错误终态；
8. 在所有路径清理临时目录。

原 `compile_ingested_task()` 和重编译端点内部 `_compile_task()` 不再作为两套独立执行路径存在。

### 7.2 子进程合同

继续采用：

```text
sys.executable -m scripts.compile <doc_id>
```

并保留：

- `cwd=BASE_DIR`；
- 继承父进程环境；
- `PYTHONUTF8=1`；
- `capture_output=True`；
- `text=True`、`encoding="utf-8"`、`errors="replace"`，与子进程 `PYTHONUTF8=1` 保持一致；
- `check=False`。

成功判定必须同时满足：

1. 子进程成功启动；
2. 子进程返回码为 0；
3. 最终 `raw/{doc_id}.meta.yaml` 的 `status` 为 `compiled`。

任何一项不满足都进入失败处理。特别地，返回码为 0 但最终状态仍为 `raw`、`compiling`、`error` 或缺失时，不得视为成功。

### 7.3 状态初始化失败

缺少 `scripts/compile.py`、`subprocess.run()` 抛出 `OSError`、LLM 初始化在进入 `compile_doc()` 前失败，或子进程在未产生终态时退出，都必须由包装器写入 `error`，不得静默返回。

## 8. 快照与回滚

### 8.1 快照范围

每次 API 来源编译开始前，记录下列可能被本轮覆盖的产物：

```text
wiki/{doc_id}.summary.yaml
wiki/index.yaml
meta/ontology/{doc_id}.ontology.yaml
meta/ontology/global_ontology.yaml
meta/relations/{doc_id}.relations.yaml
meta/relations/knowledge_graph.yaml
meta/ontology/entity_relations.yaml
```

对每个路径记录：

- 原文件存在：保存原始字节；
- 原文件不存在：记录 `missing`。

`raw/{doc_id}.meta.yaml` 不做整体快照回滚，因为它必须保留本轮最新管理状态和错误字段。

### 8.2 快照位置

使用系统临时目录，例如 `tempfile.TemporaryDirectory()`，不得写入仓库受版本控制目录。快照以仓库相对路径作为清单键，避免路径混淆。

### 8.3 成功路径

最终状态明确为 `compiled` 时：

- 保留本轮新产物；
- 清除元数据错误字段；
- 删除临时快照。

### 8.4 失败路径

编译失败时：

1. 原来存在的文件按原始字节恢复；
2. 原来不存在、本轮新生成的文件删除；
3. 恢复采用同目录临时文件加 `os.replace()`，避免把恢复动作本身变成明显的部分写入；
4. 完成恢复后写入最终 `status=error`；
5. 清理临时快照。

效果：

- 首次编译失败：新摘要、本体和索引条目不残留；
- 重编译失败：旧摘要、旧索引、旧本体和旧关系逐字节恢复。

### 8.5 回滚失败

若任一文件无法恢复：

```yaml
status: error
error_code: rollback_failed
error_message: <编译原因及未恢复文件的脱敏摘要，最多500字符>
```

不得将文档标记为 `compiled`。前端显示“编译失败，旧版本恢复异常，请联系管理员”。

### 8.6 关系检测边界

当前 `compile.py` 将关系检测子进程失败仅视为 warning，并仍可能将文档标记为 `compiled`。E004 不改变这一核心语义：只要编译脚本最终明确写为 `compiled`，包装器按成功处理，不把关系 warning 升级为整体失败。

## 9. 并发模型

### 9.1 调度锁

设置短时进程内调度锁，保护：

```text
读取状态 -> 判断是否冲突 -> 写入compiling -> 加入后台任务
```

同一文档已为 `compiling` 时返回 `409 compile_in_progress`。

### 9.2 全局执行锁

设置进程内全局执行锁，包住：

```text
快照 -> 子进程编译 -> 终态确认 -> 保留或回滚 -> 快照清理
```

即使文档 ID 不同，也必须在当前 API 进程内串行执行，因为不同文档共享写入：

- `wiki/index.yaml`；
- `global_ontology.yaml`；
- `knowledge_graph.yaml`；
- `entity_relations.yaml`。

否则一个失败任务的回滚可能覆盖另一个成功任务的结果。

### 9.3 明确边界

该锁不解决：

- 多 worker API 进程；
- 多机器部署；
- 独立 CLI 编译进程；
- API 进程崩溃或强制终止。

这些仍属于共享 YAML 和持久任务治理的后续风险。

## 10. 错误分类与脱敏

### 10.1 稳定错误码

```text
compile_in_progress
llm_configuration
service_unavailable
timeout
document_processing
compile_failed
rollback_failed
```

`compile_in_progress` 仅用于同步冲突响应；其他代码用于后台终态。

### 10.2 分类原则

分类以异常类型、子进程返回结果、最终元数据及每个流末尾最多 8192 个字符的 stdout/stderr 为输入：

- 缺少模型依赖、API Key 或明显配置错误 -> `llm_configuration`；
- 连接失败、服务不可达、上游 5xx 等 -> `service_unavailable`；
- 明确超时异常或超时信息 -> `timeout`；
- 原始文本缺失、无法解析或内容处理失败 -> `document_processing`；
- 其他编译失败或未产生合法终态 -> `compile_failed`；
- 快照恢复不完整 -> `rollback_failed`。

分类器必须有统一兜底，不能把未知异常直接作为前端文案。

### 10.3 技术信息脱敏

写入 `error_message` 前按固定顺序处理：

1. 合并异常摘要、必要的截断 stdout/stderr 和元数据错误；
2. 删除或替换 `Authorization`、`Bearer`、API Key、常见 `sk-...` 样式密钥；
3. 清理 URL 查询参数中的敏感值；
4. 把换行和连续空白压缩为单行；
5. 不保存完整堆栈、完整请求体或完整响应体；
6. 最多保留 500 个字符。

后端日志同样不得主动打印未脱敏密钥或完整认证头。

## 11. 前端设计

### 11.1 API 客户端

`frontend/src/lib/api.ts` 增加：

```ts
type CompileErrorCode =
    | 'compile_in_progress'
    | 'llm_configuration'
    | 'service_unavailable'
    | 'timeout'
    | 'document_processing'
    | 'compile_failed'
    | 'rollback_failed';
```

`DocMeta` 增加：

```ts
error_code?: CompileErrorCode;
```

增加结构化 `ApiError` 或等价实现，使 `handleResponse()` 尝试读取非 2xx JSON 中的 `detail.code`，而不是只构造 `API error 409: Conflict`。

统一友好映射：

```text
compile_in_progress  -> 该文档正在编译，请稍后再试
llm_configuration    -> 模型服务暂不可用，请联系管理员检查配置
service_unavailable  -> 编译服务暂不可用，请稍后重试
timeout              -> 编译服务响应超时，请稍后重试
document_processing  -> 文档编译未完成，请检查文件内容后重试
compile_failed       -> 编译失败，请稍后重试或联系管理员
rollback_failed      -> 编译失败，旧版本恢复异常，请联系管理员
未知错误              -> 编译失败，请稍后重试或联系管理员
```

前端不得展示 `error_message`、HTTP 状态码、异常类名、环境变量名、文件路径、子进程输出或堆栈。

### 11.2 知识库页面轮询

现有条件保持：

```ts
const hasPending = docs.some(
    doc => doc.status === 'raw' || doc.status === 'compiling'
);
```

行为改为：

- 有 pending：每 3 秒刷新；
- 无 pending：停止轮询；
- 组件卸载：清理 timer；
- 轮询请求失败：保持当前列表，不用原始技术错误打扰用户；
- 页面提示改为“有文档编译中，每 3 秒自动刷新…”。

### 11.3 状态转换通知

页面保存上一轮文档状态快照：

- 首次加载时已经为 `error` 的历史文档只显示行内提示，不弹 Toast；
- 轮询检测到 `raw/compiling -> error` 时，按 `error_code` 弹一次 Toast；
- 同一失败终态在后续轮询中不重复提示；
- 用户再次重编译后状态进入 `compiling`，该轮错误已提示标记重置；
- 新一轮再次失败时允许再次弹一次 Toast。

### 11.4 文档卡片

保留现有状态徽标：

```text
raw       -> 待编译
compiling -> 编译中
compiled  -> 已编译
error     -> 编译失败
```

`status=error` 时，在摘要下方显示按 `error_code` 映射的行内友好说明，并继续显示重编译按钮。

### 11.5 上传页

上传成功仍显示“摄入成功，后台编译中”，并引导用户前往知识库仪表盘查看进度。

上传请求本身的即时失败改为统一友好文案，不直接渲染 `Error.message`。上传页不建立第二套后台轮询逻辑。

## 12. 组件变更边界

### 12.1 预计后端文件

- `api/main.py`
  - 统一调度和执行入口；
  - 状态准备；
  - 两类进程内锁；
  - 快照、子进程检查、回滚；
  - 错误分类和脱敏；
  - `error_code` 目录投影；
  - 重编译 409 合同。
- `scripts/doc_admin.py`
  - 提供或调整安全的元数据读取与状态写入；
  - `recompile_doc()` 不再先写回 `raw`。
- `scripts/compile.py`
  - 原则上不改变核心编译步骤；仅在实现计划证明必要时做最小、向后兼容调整。
- `tests/test_api.py` 及必要的专用后台任务测试文件。

若 `api/main.py` 继续增长导致测试边界不清，允许把纯函数式的快照、错误分类和脱敏逻辑提取到一个新的聚焦模块；不得借机进行无关重构。

### 12.2 预计前端文件

- `frontend/src/lib/api.ts`
- `frontend/src/app/wiki/page.tsx`
- `frontend/src/components/WikiCard.tsx`
- `frontend/src/components/UploadZone.tsx`
- 对应 Jest 测试文件

不新增页面、状态管理框架、通知协议或管理员技术详情组件。

## 13. 测试策略

### 13.1 后端 TDD 合同

至少覆盖：

1. 上传摄入成功后，响应返回前元数据已为 `compiling`。
2. 上传和重编译都调度同一后台入口。
3. `compiling` 文档再次重编译返回 `409 + compile_in_progress`，不创建第二任务。
4. 编译脚本缺失写入 `error`，不静默返回。
5. 子进程启动失败写入 `error`。
6. LLM 配置缺失归类为 `llm_configuration`。
7. 网络连接问题归类为 `service_unavailable`。
8. 明确的上游请求超时或超时异常归类为 `timeout`。
9. 文档处理问题归类为 `document_processing`。
10. 未知或未产生终态的失败归类为 `compile_failed`。
11. 技术信息去除 API Key、Bearer、Authorization 和敏感查询参数。
12. 技术信息压缩为单行且不超过 500 字符。
13. 编译成功最终为 `compiled` 并清除旧错误字段。
14. 返回码为 0 但最终状态不是 `compiled` 时仍判失败。
15. 首次编译失败不留下索引条目或单文档半成品。
16. 重编译失败后，旧摘要、索引、本体和关系文件逐字节恢复。
17. 回滚失败写入 `rollback_failed`。
18. 两个不同文档的 API 后台编译在当前进程内串行。
19. 所有测试使用 `tmp_path`、monkeypatch 和假子进程，不访问网络或真实模型。
20. 测试不修改真实 `originals/`、`raw/`、`wiki/`、`meta/` 数据。

### 13.2 前端 TDD 合同

至少覆盖：

1. `409 detail.code=compile_in_progress` 被解析为结构化客户端错误。
2. 每个稳定错误码映射为固定中文友好提示。
3. 未知错误使用统一兜底文案。
4. UI 不出现 `409`、`Conflict`、异常类型、环境变量名或原始技术信息。
5. 错误卡片显示友好行内说明。
6. 有 pending 文档时每 3 秒刷新。
7. 全部文档终态后停止轮询。
8. 首次加载已有错误文档不弹 Toast。
9. 轮询检测到 `compiling -> error` 只弹一次。
10. 再次重编译后，新一轮失败可以再次提示。
11. 重复重编译显示“该文档正在编译，请稍后再试”。
12. 上传即时失败显示友好提示。
13. 使用 fake timer 或受控请求，不真实等待 3 秒。
14. Jest 不产生 `act()` 警告或非预期 `console.error`。

### 13.3 完整验证

实施阶段必须执行并记录：

- 后端目标测试；
- `pytest tests/ -q`，在无 Key、黑洞代理环境下通过；
- 前端目标 Jest；
- 完整 Jest；
- touched-file ESLint；
- Next.js 生产 build；
- `git diff --check`；
- 变更范围核验；
- 真实数据目录 before/after manifest。

数据 manifest 覆盖：

```text
originals/
raw/
wiki/
meta/
```

路径、大小和 `mtime_ns` 必须一致。

## 14. 验收标准

E004 完成时必须证明：

1. 上传或重编译接口返回后，文档立即显示“编译中”。
2. 同一文档重复重编译被拒绝，前端只显示友好提示。
3. 后台成功最终显示“已编译”。
4. 后台失败在轮询刷新后显示“编译失败”和分类友好原因。
5. 前端不显示 HTTP 状态码、原始异常或技术错误详情。
6. 首次编译失败不污染检索索引。
7. 重编译失败后旧版本仍可搜索、问答和查看。
8. 后端元数据保存脱敏、单行、最多 500 字符的技术原因。
9. 返回码为 0 但没有 `compiled` 终态时不会误报成功。
10. 当前单 API 进程内的 API 来源编译全局串行。
11. 所有自动化测试、现有 CI 门禁和数据零污染验证通过。

## 15. 风险与后续工作

E004 后仍存在：

- 多 worker 或多机器部署中的共享 YAML 并发覆盖；
- API 与外部 CLI 同时编译的跨进程竞争；
- API 进程自身崩溃、被强制终止或子进程永久挂起后，文档仍可能长期停留在 `compiling`；硬看门狗、进程树终止和启动恢复扫描留给后续任务；
- 关系检测 warning 仍可能留下部分关系结果，但不阻断主编译；
- YAML 写入尚未全面采用通用原子写入和文件锁。

后续任务应独立评估持久任务记录、跨进程锁、启动恢复扫描和共享 YAML 通用原子化，不并入 E004。

## 16. 实施门禁

本设计文档获用户书面确认后，下一步仅调用 `writing-plans` 形成详细实施计划。实施仍须遵循：

- 一个任务 ID、一个实现分支、一个独立 worktree、一个 PR；
- TDD：先写失败测试，再做最小实现；
- Kimi K3 实施，Codex GitHub Review 独立审查，GPT 追加审查；
- 必需 CI：`repository-integrity`、`python-core`、`frontend-unit-build`；
- 禁止直接推送 `dev`、force、reset、clean、rebase、amend、`--admin` 和删除受保护分支；
- 若 `dev` 前进，普通 merge `origin/dev` 到任务分支并重新验证、重新审查；
- 未经后续明确授权，不创建实现分支、不修改业务代码、不创建实现 PR、不合并。
