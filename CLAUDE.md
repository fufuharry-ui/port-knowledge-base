# CLAUDE.md

> Version: 1.3 | Updated: 2026-08-02

本文件定义 Claude Code 在本仓库中的开发权限、工作流程、架构边界和验证要求。

Claude Code 是项目的实施和审查工具，不是产品负责人、架构决策人或项目路线图制定者。

---

## 1. 指令优先级

出现冲突时，按以下顺序执行：

1. 用户在当前会话中给出的最新、最具体的明确指令；
2. 当前任务合同：
   - 用户在当前会话中直接提供的任务合同；或
   - `docs/dev/tasks/<task-id>.md` 中实际存在的任务合同；
3. 本文件 `CLAUDE.md`；
4. `ANTIGRAVITY.md` 中的产品理念、领域语义、数据结构和知识处理约束；
5. 当前任务对应的验收测试和接口契约；
6. 当前代码；
7. 历史计划、报告、日志和旧文档。

`docs/dev/tasks/` 当前可以尚未建立。用户在当前会话中提供的、包含完整范围和验收条件的任务合同具有同等效力。

如果任务合同文件不存在，同时用户也没有给出明确修改范围：

- 只允许读取、分析和报告；
- 不得自行从 `docs/plans/`、历史日志或代码中推导修改任务；
- 不得自动降级为依据本文件自行实施；
- 应停止并请求明确任务范围。

如果测试、代码和文档之间冲突，不得擅自选择一方覆盖另一方。应说明冲突、影响和建议处理方式，等待用户决定。

---

## 2. 每次会话启动协议

开始任何任务前必须执行：

```bash
git branch --show-current
git status --short
git log -5 --oneline
git rev-parse --show-toplevel
```

然后确认：

1. 当前工作目录和worktree；
2. 当前分支；
3. 当前任务编号和角色；
4. 允许修改文件；
5. 禁止修改文件；
6. 验收命令；
7. 是否获得Git写操作授权；
8. 工作区是否存在来源不明的修改。

如果工作区存在非本任务产生的修改：

* 不得覆盖；
* 不得暂存；
* 不得还原；
* 不得清理；
* 不得继续修改同一文件；
* 应立即报告。

任务明确、范围完整时，不要反复询问。存在会影响正确性的实质歧义时，停止修改并报告。

---

## 3. 任务合同

每个开发任务必须具备：

* 唯一任务编号；
* 问题描述；
* 目标结果；
* 非目标；
* 允许修改文件；
* 禁止修改文件；
* 验收命令；
* Git权限；
* 完成报告要求。

只允许修改任务合同明确列出的文件。

如果实施中发现必须修改额外文件：

1. 停止修改；
2. 说明原范围为什么不足；
3. 列出拟新增文件；
4. 说明不修改的影响；
5. 等待用户重新授权。

不得进行：

* 顺手格式化；
* 无关变量改名；
* 无关依赖升级；
* 无关目录调整；
* 全仓批量修改；
* 无关技术债清理；
* 未经任务授权的架构重构。

---

## 4. 架构边界

### 4.1 核心知识处理层

`scripts/` 包含核心知识处理能力：

* `ingest.py`：文档摄入；
* `compile.py`：摘要和本体编译；
* `search.py`：分层检索和回答；
* `relate.py`：文档及实体关系处理；
* `ontology.py`：本体和实体关系能力；
* `consistency.py`：跨文档一致性检查；
* `doc_admin.py`：文档生命周期管理。

这些文件不是永久冻结文件，但只有任务合同明确允许时才能修改。

修改核心脚本时必须：

* 先增加能够暴露原问题的测试；
* 保留既有外部接口，除非任务明确要求改变；
* 在隔离目录中验证；
* 检查真实数据目录未被污染；
* 说明对摄入、编译、检索、关系和数据产物的影响。

### 4.2 HTTP服务层

当前存在两套FastAPI入口：

* `api/main.py`：当前前端实际使用的后端；
* `app/`：模块化后端候选实现，尚未与 `api/main.py` 统一。

任何涉及后端的任务合同必须明确指定：

```text
target_backend: api | app | both
```

没有明确指定时：

* 不得自行选择；
* 不得默认修改 `api/`；
* 不得为了“保持一致”自动同时修改两套后端；
* 应停止并请求用户确认。

`both` 只有用户明确授权时才允许使用。

### 4.3 前端

`frontend/` 是Next.js App Router应用。

修改前端前必须先阅读：

```text
frontend/AGENTS.md
```

当前Next.js版本可能包含破坏性变化。写代码前应优先读取当前安装版本的：

```text
frontend/node_modules/next/dist/docs/
```

不得仅凭模型训练记忆使用旧版Next.js API。

### 4.4 产品规范

`ANTIGRAVITY.md` 是产品理念、知识处理流程、数据结构、doc_id格式和领域语义的重要依据。

本文件主要约束开发流程和工程边界。

当 `ANTIGRAVITY.md` 与当前代码或任务合同不一致时，不得自行修改任一方，应报告差异。

---

## 5. 核心产品原则

项目坚持：

* 文档编译优于简单切片；
* Context Stuffing分层检索；
* 本体、实体关系和文档关系共同增强理解；
* 回答必须支持引用溯源；
* 不引入外部分布式向量数据库作为主架构。

Embedding只能作为可选检索增强能力。

目标契约（E002 已实现并验证）：

```text
Embedding配置有效且调用成功
→ 关键词与向量混合检索

Embedding未配置、初始化失败或调用失败
→ 自动退化为BM25关键词检索
→ 基础检索不得因此整体失败
```

E002 验证事实（详见 `docs/dev/tasks/E002-embedding-optional-fallback.md`）：

* 降级由 `scripts/search.py` 的 `layer1_filter` 编排层负责：先完成 BM25，再尝试 Embedding 路径；Embedding 初始化或调用异常被捕获并记录不含密钥的 warning，随后按 BM25 原始分数降序返回 top_k；BM25 无命中返回空列表；
* `VectorEngine(docs, client=None)` 支持可选客户端注入；不传 client 时行为与原有一致；
* `scripts/embedding_client.py` 保持严格：直接实例化 `EmbeddingClient` 且无 `EMBEDDING_API_KEY` 时仍在构造函数抛出 `RuntimeError`，不得把它改成全局静默 no-op 客户端；
* Embedding 可用时混合检索（RRF 融合、向量-only 召回阈值、top_k）行为不变；
* 离线检索合同由 `tests/test_embedding_fallback.py` 与 CI `python-core` 的离线检索步骤守护。

---

## 6. 数据目录和生成产物

以下目录包含真实知识资产或生成产物：

* `originals/`
* `raw/`
* `wiki/`
* `meta/`

除非任务明确允许，不得：

* 批量改写；
* 删除；
* 格式化；
* 重建；
* 通过测试写入；
* 将UAT临时产物写入真实目录；
* 手工修改生成数据伪造测试成功。

单元测试必须使用 `tmp_path`、隔离目录或明确的测试副本。

---

## 7. 共享状态与数据完整性

以下文件属于共享状态：

* `wiki/index.yaml`
* `meta/ontology/global_ontology.yaml`
* `meta/ontology/entity_relations.yaml`
* `meta/relations/knowledge_graph.yaml`
* `meta/consistency/contradictions.yaml`
* `raw/*.meta.yaml`

涉及这些文件的修改必须考虑：

* 并发写入；
* 丢失更新；
* 半成品状态；
* 原子替换；
* 文件锁；
* 失败恢复；
* 重试幂等；
* 重编译；
* 删除和重新摄入。

新增或修改共享文件写入代码时，默认要求：

1. 在同目录写入临时文件；
2. 完整写入并刷新后，通过原子替换发布；
3. 需要跨进程并发时使用明确文件锁或串行任务约束；
4. 重试不得产生重复索引、节点或关系；
5. 失败不得破坏旧的有效版本；
6. 必须增加失败和并发边界测试。

不得继续新增无保护的“读取—修改—直接覆盖”共享文件流程，除非任务明确只处理单线程隔离场景并说明限制。

---

## 8. 文档ID和生命周期

文档ID外部格式保持：

```text
doc_{YYYYMMDD}_{seq:03d}
```

当前实现可能通过同日文件数量生成序号，该方式存在编号空洞和并发冲突风险，尚未完成修复。

涉及文档ID的任务必须覆盖：

* 中间编号被删除；
* 已有编号不连续；
* 同时上传；
* 重复文件；
* 解析失败；
* 重试；
* 重编译；
* 删除后再次摄入。

文档状态至少包括：

```text
raw
compiling
compiled
error
deleted
```

状态变化必须可追踪，后台失败不得无声吞掉。

---

## 9. TDD和测试隔离

功能开发和Bug修复默认采用：

```text
失败测试
→ 确认失败原因
→ 最小实现
→ 目标测试通过
→ 相关回归测试
→ 完整验证
```

测试必须证明行为，而不是只提高覆盖率。

单元测试不得访问：

* 真实LLM API；
* 真实Embedding API；
* 外部网络；
* 用户真实 `.env`；
* 生产密钥；
* 真实知识库目录。

涉及LLM、Embedding或其他外部服务时：

* 必须显式使用确定性的mock或stub；
* 必须为预期调用提供明确返回值；
* 不得依赖开发机环境变量恰好存在；
* 不得使用未配置的通用MagicMock链条作为业务成功证据；
* 应断言不会发生真实网络请求。

`scripts/` 中任何文件路径必须通过模块级路径常量访问。

新增或修改模块级路径常量时，必须同步检查并更新对应的：

```text
tests/conftest.py
```

路径patch fixture。

测试不得因为遗漏patch而读取或写入真实 `raw/`、`wiki/`、`meta/` 或 `originals/`。

Bug修复测试应尽量完成红绿验证：

1. 修复前能够失败；
2. 修复后能够通过；
3. 断言针对用户可观察行为或明确接口契约。

纯文档修改可以不运行产品测试，但必须执行diff和格式检查。

---

## 10. 错误处理和后台任务

新增或修改代码不得使用以下方式隐藏失败：

```python
except Exception:
    pass
```

确需捕获宽泛异常时必须：

* 记录原始异常；
* 明确降级路径；
* 保持数据状态一致；
* 向调用方返回可判断结果；
* 增加覆盖降级行为的测试。

后台任务至少应记录：

* 任务对象；
* 排队时间；
* 开始时间；
* 当前状态；
* 完成时间；
* 错误类型和错误信息；
* 是否可重试。

API返回“已接受处理”不得描述为“已成功完成”。

不得因异常被捕获就把任务标记为成功。

---

## 11. 多轮问答和引用

每条助手消息必须独立保存：

* 回答正文；
* citations；
* 命中文档；
* 必要的生成状态。

不得使用一份全局citations状态渲染所有历史助手消息。

任何涉及多轮问答的任务至少验证：

1. 第一轮回答引用文档A；
2. 第二轮回答引用文档B；
3. 第二轮结束后第一轮仍引用文档A；
4. 停止生成不会破坏已接收内容；
5. 新请求不会覆盖旧消息状态；
6. 引用跳转仍指向对应文档。

~~当前 `frontend/src/components/ChatPanel.tsx` 可能存在全局citations覆盖历史回答的已知缺陷，尚未修复。~~（E003已处理：引用、正文和生成状态迁移为assistant消息级状态，ActiveRequest请求身份守卫覆盖停止竞态与旧请求清理；历史thoughts仍不持久化，仍为当前轮临时状态；详见 `docs/dev/tasks/E003-message-scoped-citations.md`）

---

## 12. 安全要求

不得：

* 提交 `.env`；
* 提交或输出完整API Key；
* 在日志、截图、测试证据或错误信息中暴露密钥；
* 将Provider、Base URL、模型凭据或API Key写入项目级 `.claude/settings.json`；
* 使用 `--dangerously-skip-permissions`；
* 执行来源不明的远程脚本；
* 在生产环境使用任意来源CORS；
* 在无权限判断时新增危险管理接口。

展示密钥时只能使用脱敏形式，例如：

```text
sk-***abcd
```

涉及文件上传时至少考虑：

* 扩展名；
* MIME类型；
* 文件大小；
* 路径穿越；
* 重名；
* 解析失败清理；
* 恶意或异常内容。

项目级 `.claude/settings.json` 当前只允许：

```json
{
  "autoMemoryEnabled": false
}
```

本机Provider和模型配置应保留在用户级配置、环境变量或cc-switch中。

---

## 13. Git规则

当前Git分支基线事实：

* `origin/main` 与 `origin/dev` 没有共同祖先；
* `origin/master` 还存在另一组历史；
* `dev` 是当前正式集成和PR目标分支；
* `main` 和 `master` 保留为隔离旧历史，不得作为普通开发、merge或rebase基线；
* 不得自行重建、覆盖、统一或重写历史；
* 新的工程任务以最新 `origin/dev` 为基线创建独立分支和worktree。

未经用户明确授权，禁止执行：

```text
git commit
git push
git push --force
git push --force-with-lease
git merge
git rebase
git reset
git clean
git tag
git branch -D
git update-ref
git symbolic-ref
git push origin --delete
gh pr create
```

也不得：

* 直接在 `main` 或 `dev` 上提交；
* 修改Git历史；
* 删除分支；
* 修改 `.gitignore` 以隐藏当前任务产生的文件；
* 清理来源不明的未跟踪文件。

允许用于只读分析的命令包括：

```text
git status
git diff
git log
git show
git branch --show-current
git merge-base
git rev-parse
git ls-files
```

每个任务原则上使用：

* 独立任务编号；
* 独立分支；
* 独立worktree；
* 独立PR；
* 独立审查会话。

Git写操作必须由用户在当前任务中明确授权。

---

## 14. 模型、角色和会话

建议角色分工：

* Kimi K3：实施、测试和Bug修复；
* Codex GitHub Review：独立PR审查，检查当前Head的正确性、安全性、测试隔离和范围一致性；
* 用户：任务批准、重要审查意见取舍和最终验收。

Codex审查流程约定：

* 使用PR评论 `@codex review` 触发审查；
* Codex意见必须先验证，再决定修复或技术性回复，不得盲目执行；
* 修改Head后必须重新执行required checks，并对新Head重新触发Codex审查；
* Codex无问题结论必须对应当前Head，且Codex不能替代CI；
* 不再将GLM作为默认审查模型。

以下任一变化必须新开Claude Code会话：

* 更换模型；
* 更换任务编号；
* 更换分支；
* 更换worktree；
* 更换PR；
* 从实施切换为审查；
* 从审查切换为实施；
* 上下文已经多次压缩且任务事实可能失真。

审查角色默认只输出意见，不直接修改文件。只有用户明确授权时才能切换为实施角色。

中高风险任务应使用异构模型完成“计划—实施—独立审查”。

低风险纯文档或机械性修改可以由一个实施模型执行，再由用户直接验收，无须强制三角色闭环。

---

## 15. 关键运行事实和命令

当前Windows开发机常用Python解释器：

```text
D:\ProgramData\anaconda3\python.exe
```

不得假设CI或其他机器使用相同绝对路径。

### Python依赖和导入

```bash
python -m pip install -r requirements.txt
python -c "import api.main"
```

### Python测试

```bash
python -m pytest tests/ -q
python -m pytest tests/test_target.py -v
```

### FastAPI

```bash
uvicorn api.main:app --reload --port 8000
python -m app.main
```

### 前端

```bash
cd frontend
npm ci
npm test -- --runInBand
npm run lint
npm run build
npm run test:e2e
```

### 真实栈UAT

只有任务明确要求、隔离环境已准备并且真实API配置可用时，才能运行：

```bash
cd frontend
npm run test:e2e:live
```

真实栈UAT不得污染源数据。

### 配置事实

根目录 `.env` 通过多个模块中的轻量解析逻辑加载，并使用：

```python
os.environ.setdefault(...)
```

已存在的真实环境变量优先于 `.env`。

主要模型变量：

```text
COMPILE_MODEL
ONTOLOGY_MODEL
RELATE_MODEL
SEARCH_MODEL
```

Embedding变量：

```text
EMBEDDING_API_KEY
EMBEDDING_BASE_URL
EMBEDDING_MODEL_NAME
```

当前不同模块的默认模型可能存在漂移。不得在未读取对应代码和 `.env.example` 前假设默认模型一致。

### 关键调用链

`compile.py` 完成摘要、本体和索引更新后，会通过子进程触发 `relate.py`。

`/api/v1/qa` 的SSE事件顺序应保持：

```text
thought
→ source
→ entity
→ delta
→ done
```

修改SSE契约时必须同步更新前端解析器和测试。

---

## 16. 验证和完成声明

任何“已完成”“已修复”“测试通过”“构建成功”的声明，必须由当前会话中刚刚运行的完整命令支持。

以下内容不能代替新鲜验证：

* 历史日志；
* 旧截图；
* 提交说明；
* 其他模型报告；
* “应该通过”；
* “看起来没问题”。

完成报告必须包含：

### 修改文件

列出全部修改文件和每个文件的作用。

### 根因

说明问题为什么发生。

### 实施内容

说明实际做了什么。

### 验证证据

逐项列出：

* 命令；
* 退出码；
* 通过数量；
* 失败数量；
* 跳过数量；
* 未运行项目；
* 环境限制。

### 范围检查

提供：

```bash
git status --short
git diff --stat
git diff --check
```

并说明是否存在任务范围外改动。

### 剩余风险

说明尚未解决、尚未验证或依赖外部环境的事项。

不得隐藏失败、跳过项、未验证项或异常降级。

---

## 17. 当前已知工程风险

以下风险必须通过独立任务处理，不得在无授权时顺手修复：

1. `main`、`dev`、`master` 的Git历史基线异常；
2. ~~依赖文件与真实运行依赖可能不一致~~（E001已处理：`requirements.txt` 已显式覆盖全部真实直接运行与测试依赖，干净环境 install + `pip check` 在本地 Python 3.12 与 CI Python 3.11 均通过）；
3. ~~Embedding尚未实现可靠可选降级~~（E002已处理：降级由 `layer1_filter` 编排层负责，BM25 为基础检索、Embedding 为可选增强；`EmbeddingClient` 直接使用仍保持严格；离线检索合同测试与 CI 离线检索门禁已建立，详见 `docs/dev/tasks/E002-embedding-optional-fallback.md`）；
4. ~~pytest离线边界需要重新验证~~（E001已完成无密钥干净环境完整审计：237通过/7失败；6项为Embedding缺失类归入E002，1项为`test_consistency_post_triggers_check`对LLM Key的隐式依赖，登记为后续测试隔离债务；该债务已由T001处理：`test_consistency_post_triggers_check`与`test_consistency_post_degrades_on_error`均显式mock `get_llm_client`并固定`RELATE_MODEL`，无密钥、无`.env`、黑洞代理环境下完整pytest 255项全绿，生产代码零变更；详见 `docs/dev/tasks/E001-dependency-startup-baseline.md` 与 `docs/dev/tasks/T001-full-pytest-closure.md`）；
5. `api/main.py` 与 `app/` 两套后端分叉；
6. 共享YAML存在并发覆盖和半成品风险；
7. 文档ID生成存在编号空洞和并发风险；
8. ~~多轮问答引用可能使用全局状态~~（E003已处理：引用、正文和生成状态迁移为assistant消息级状态，ActiveRequest请求身份守卫覆盖停止竞态与旧请求清理；历史thoughts仍不持久化；详见 `docs/dev/tasks/E003-message-scoped-citations.md`）；
9. 后台编译任务缺少可靠状态和错误记录；
10. 测试指南、路线图和当前代码可能存在漂移；
11. GitHub Actions门禁已建立：`repository-integrity`、`python-core`、`frontend-unit-build` 三个required checks；E001已在 `python-core` 中增加 `pip check` 和无密钥startup smoke；E002已在 `python-core` 的 startup smoke 之后增加真正离线检索门禁（无密钥+黑洞代理运行 `test_embedding_fallback`、`test_search`、`test_hybrid_search`、`test_api_qa` 四文件）；T001已在 `python-core` 的99项确定性核心测试之后新增 `Run full backend test suite`（无密钥+黑洞代理运行完整 `pytest tests/ -q`）作为最终后端门禁，startup smoke、离线检索、99项核心测试保留为分层诊断步骤；完整pytest绿色不得解释为前端E2E或真实栈UAT已完成。

---

## 18. 完成标准

一个任务只有同时满足以下条件才能提交审查：

* 修改范围符合任务合同；
* 原问题已由失败测试或明确验证步骤证明；
* 实施后目标测试通过；
* 相关回归测试通过；
* 离线测试未访问真实外部服务；
* 未污染真实知识库数据；
* 文档、配置、代码和测试一致；
* Git diff中不存在无关修改；
* 完成报告包含新鲜验证证据；
* 用户明确决定是否提交、推送和创建PR。

条件未满足时，只能报告实际进度，不得宣称任务完成。
