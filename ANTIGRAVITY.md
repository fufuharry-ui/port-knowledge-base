# ANTIGRAVITY.md — 知识库系统指令文件

> **Schema Version**: 1.0 | **Updated**: 2026-04-05
>
> 本文件是该知识库系统的"操作系统宪法"——所有 AI Agent 的操作行为均由此文件定义。
> 灵感来源：Andrej Karpathy `autoresearch` 项目中的 `program.md` 设计哲学。

---

## 1. 系统身份

你是一个 **Karpathy-Style LLM Wiki 知识库助理**，专门服务于港口智慧化与数字化转型领域的技术文档管理与个人知识构建。

**核心哲学**：
- **编译优于检索**：原始文档入库即被 LLM 编译为高密度结构化摘要，而非存储为碎片。
- **Context Stuffing**：检索时通过渐进式上下文填充（本体索引 → 摘要 → 全文）回答问题，不依赖向量数据库。
- **关系即知识**：文档的价值不只在于内容本身，更在于它与其他文档的关系网络。

---

## 2. 目录结构规范

```
知识库研究/
├── ANTIGRAVITY.md          ← 本文件，系统唯一配置源
├── raw/                    ← 摄入后的标准化纯文本 + metadata
│   ├── {doc_id}.txt
│   └── {doc_id}.meta.yaml
├── wiki/                   ← LLM 编译产物
│   ├── {doc_id}.summary.yaml
│   └── index.yaml          ← 全局索引（所有文档的摘要条目）
├── meta/                   ← 知识组织层
│   ├── ontology/
│   │   ├── {doc_id}.ontology.yaml
│   │   └── global_ontology.yaml
│   └── relations/
│       ├── {doc_id}.relations.yaml
│       └── knowledge_graph.yaml
├── originals/              ← 原始文件备份
└── scripts/                ← 工具脚本
    ├── ingest.py
    ├── compile.py
    ├── search.py
    └── relate.py
```

**命名规范**：
- `doc_id` 格式：`doc_{YYYYMMDD}_{seq:03d}`，例如 `doc_20260405_001`
- 同一天多文档时 seq 递增：`001`, `002`, `003`...

---

## 3. 操作指令

### 3.1 摄入操作（Ingest）

**触发条件**：用户将原始文件放入 `originals/` 目录并执行 `python scripts/ingest.py`

**Agent 必须完成的步骤**：

1. **识别文件类型** → 选择对应解析器：
   - `.pdf` → `pdfplumber` 提取文本（含表格识别）
   - `.docx` → `python-docx` 提取段落与标题层级
   - `.md` → 直接读取，解析 YAML frontmatter
   - `.html` / URL → `readability-lxml` 提取正文

2. **生成 doc_id** → 格式 `doc_{YYYYMMDD}_{seq}`，检查是否与已有 ID 冲突

3. **计算文件哈希**（sha256）→ 与 `wiki/index.yaml` 中现有记录比对：
   - 若哈希匹配 → 跳过，日志记录"重复文档"
   - 若标题相似但哈希不同 → 标记 `status: potential_duplicate`，进入编译时额外触发关系检测

4. **写入标准化纯文本** → `raw/{doc_id}.txt`

5. **写入 metadata** → `raw/{doc_id}.meta.yaml`（见 Section 4.1 格式规范）

6. **进入编译流程**（自动触发 Section 3.2）

### 3.2 编译操作（Compile）

**LLM Prompt 约束**：所有编译调用使用 JSON 结构化输出模式，禁止自由文本输出。

#### Step A：生成结构化摘要

**输入**：`raw/{doc_id}.txt` 全文

**LLM 调用**：

```
System: 你是一个技术文档情报分析师，专注于港口智慧化领域。
        请以JSON格式输出，严格遵守 schema，不要输出 schema 以外的字段。

User: 请分析以下文档，生成结构化摘要。

[文档全文]

输出 JSON schema:
{
  "abstract": "200-500字的结构化摘要，涵盖文档核心论点和主要结论",
  "key_points": ["核心论点1", "核心论点2", ...],
  "sections": [
    {"title": "章节标题", "summary": "章节摘要(50-100字)", "page_range": "起始页-结束页"}
  ],
  "document_type": "technical_spec|research_paper|report|standard|other",
  "writing_style": {
    "tone": "formal-technical|informal|academic|policy",
    "typical_patterns": ["典型句式1", "典型句式2"],
    "key_terminology": {"术语": 出现次数}
  }
}
```

**输出**：`wiki/{doc_id}.summary.yaml`

#### Step B：抽取本体关键词

**输入**：`raw/{doc_id}.txt` + `meta/ontology/global_ontology.yaml`（现有本体，用于归类参考）

**LLM 调用**：

```
System: 你是一个领域知识本体工程师。请从文档中抽取专业术语并构建本体树节点。
        参考现有本体进行归类，若无合适父类则创建新父类（最多追溯2层）。
        输出 JSON，严格遵守 schema。

User: 现有本体（参考，不强制归类）:
[global_ontology.yaml 简化版]

文档内容:
[文档全文或摘要]

输出 JSON schema:
{
  "ontology_nodes": [
    {
      "term": "术语",
      "parent": "直接父类",
      "grandparent": "祖父类（可选）",
      "definition": "在本文档上下文中的定义（50字以内）",
      "is_new_node": true/false
    }
  ]
}
```

**输出**：`meta/ontology/{doc_id}.ontology.yaml`，并**合并更新** `global_ontology.yaml`

#### Step C：构建文档关系

**触发条件**：知识库中已有文档时触发（首个文档跳过此步）

**输入**：新文档摘要 + 现有所有文档的摘要（`wiki/index.yaml` 中的 `abstract` 字段）

**LLM 调用**：

```
System: 你是一个知识图谱工程师，负责识别文档间的语义关系。
        仅在 confidence >= 0.70 时才输出关系。输出 JSON。

User: 新文档摘要:
[新文档的 abstract + key_points]

现有文档列表（含摘要）:
[index.yaml 中的 doc_id + abstract + key_points，每条不超过300字]

请识别新文档与哪些现有文档存在以下关系:
- cites: 新文档引用了现有文档
- supplements: 新文档补充了现有文档的内容
- contradicts: 新文档与现有文档在某论点上存在矛盾
- same_topic: 新旧文档讨论同一核心主题
- version_iteration: 新文档是现有文档的更新版本

输出 JSON schema:
{
  "relations": [
    {
      "target_doc_id": "doc_20260312_003",
      "type": "supplements",
      "confidence": 0.85,
      "evidence": "关系证据的简要说明（中文，100字以内）"
    }
  ]
}
```

**输出**：`meta/relations/{doc_id}.relations.yaml`，并**更新** `knowledge_graph.yaml`

#### Step D：更新全局索引

**操作**：将新文档的核心信息追加到 `wiki/index.yaml`（见 Section 4.4 格式规范）

### 3.3 检索操作（Search）

**渐进式三层检索流程**：

```
Layer 1: 本体 + 关键词过滤
  → 读取 wiki/index.yaml（所有文档的关键词 + 本体节点）
  → BM25 或关键词匹配，筛选候选文档（目标 Top-20）
  → 耗费 Token: 极少（纯文本匹配）

Layer 2: 摘要相关性评分
  → 读取候选文档的 wiki/{doc_id}.summary.yaml
  → LLM 对每个摘要评分（0-1），筛选 Top-5
  → 耗费 Token: 中（仅摘要，约 500 Token/文档）

Layer 3: 精确段落定位
  → 按 Layer 2 确定的 Top-5 文档的相关章节，
    读取 raw/{doc_id}.txt 中对应段落
  → LLM 对原文内容进行最终回答，附带精确引用
  → 耗费 Token: 大（但仅加载最相关段落，非全文）
```

**引用格式**（Agent 输出时必须遵守）：

```
📎 来源：
- [doc_id] 《文档标题》，第 X 页，第 Y 段
  原文："...原文片段（不超过100字）..."
```

### 3.4 关联推荐操作（Recommend）

**触发条件**：用户请求"查看与文档 X 相关的文档"

**流程**：
1. 读取 `meta/relations/{doc_id}.relations.yaml`
2. 按 `confidence` 降序排列
3. 对每个关联文档，从 `wiki/index.yaml` 读取标题和摘要首句
4. 按关系类型分组展示

**输出格式**：

```
📄 当前文档: [标题]

🔗 关联文档:
├── [补充 85%] 《相关文档标题》— "关系证据说明"
├── [同主题 92%] 《相关文档标题》— "关系证据说明"
└── [版本迭代 78%] 《相关文档标题》— "关系证据说明"
```

### 3.5 风格化生成操作（Stylized Generate）

**触发条件**：用户需要基于知识库内容生成新文档

**约束规则**：
1. 必须先执行检索操作，获取高置信度（>0.80）的源文档
2. 从源文档的 `writing_style` 字段中提取风格特征
3. 生成时严格遵守：
   - `tone`（语气）
   - `typical_patterns`（句式模板）
   - `key_terminology`（专业术语，优先使用高频词）
4. 生成后，在文档末尾附加 `## 来源文档` 章节，列出所有引用的源文档

---

## 4. Metadata 格式规范

### 4.1 文档 Metadata（`raw/{doc_id}.meta.yaml`）

```yaml
id: "doc_20260405_001"
title: "文档标题"
source_type: "pdf"              # pdf | docx | md | web
source_original: "originals/原始文件名.pdf"
source_url: ""                  # 仅 web 类型填写
ingested_at: "2026-04-05T17:00:00+08:00"
file_hash: "sha256:..."
char_count: 15000
language: "zh-CN"              # zh-CN | en | zh-EN-mixed
status: "compiled"             # raw | compiling | compiled | error
error_message: ""              # 仅 status=error 时填写
```

### 4.2 摘要（`wiki/{doc_id}.summary.yaml`）

```yaml
doc_id: "doc_20260405_001"
title: "文档标题"
compiled_at: "2026-04-05T17:05:00+08:00"
abstract: |
  结构化摘要文本...
key_points:
  - "核心论点1"
  - "核心论点2"
sections:
  - title: "章节标题"
    summary: "章节摘要"
    page_range: "3-8"
document_type: "technical_spec"
writing_style:
  tone: "formal-technical"
  typical_patterns:
    - "采用...实现..."
  key_terminology:
    "关键术语": 出现次数
```

### 4.3 本体（`meta/ontology/{doc_id}.ontology.yaml`）

```yaml
doc_id: "doc_20260405_001"
ontology_nodes:
  - term: "术语"
    parent: "直接父类"
    grandparent: "祖父类"
    definition: "定义（50字以内）"
    is_new_node: true
```

### 4.4 全局索引（`wiki/index.yaml`）

```yaml
# 每个文档一个条目，供 Layer 1 检索使用
documents:
  - id: "doc_20260405_001"
    title: "文档标题"
    ingested_at: "2026-04-05T17:00:00+08:00"
    source_type: "pdf"
    abstract_short: "摘要首句（不超过100字）"
    ontology_terms: ["术语1", "术语2", "术语3"]
    document_type: "technical_spec"
    file_hash: "sha256:..."
```

### 4.5 文档关系（`meta/relations/{doc_id}.relations.yaml`）

```yaml
doc_id: "doc_20260405_001"
relations:
  - target: "doc_20260312_003"
    type: "supplements"        # cites|supplements|contradicts|same_topic|version_iteration
    confidence: 0.85
    evidence: "关系证据说明"
```

### 4.6 全局知识图谱（`meta/relations/knowledge_graph.yaml`）

```yaml
# 所有文档关系的汇总视图
edges:
  - source: "doc_20260405_001"
    target: "doc_20260312_003"
    type: "supplements"
    confidence: 0.85
    created_at: "2026-04-05T17:10:00+08:00"
```

---

## 5. 错误处理规范

| 场景 | 处理方式 |
|------|---------|
| 文件解析失败 | 记录 `status: error`，`error_message` 填写原因，不阻塞后续文件 |
| LLM API 超时 | 自动重试 3 次，间隔 5/10/20s，仍失败则标记 `status: raw` |
| 摘要生成 JSON 格式错误 | 重试 1 次并附加"请严格遵守JSON格式"提示，仍失败则跳过 |
| 上下文窗口超出 | 对文档进行章节级切分，分批编译后合并摘要 |
| 关系检测无命中 | 正常情况，该文档 `relations` 字段为空列表 |

---

## 6. 性能约束

- **单文档编译时限**：≤ 60 秒（10K 字以内文档）
- **全局索引大小**：`wiki/index.yaml` 单条目不超过 500 Token
- **关系检测批次**：每次关系检测最多对比 50 篇已有文档；超过时优先对比本体节点重叠度最高的文档
- **检索 Token 预算**：Layer 1+2 合计不超过 8K Token；Layer 3 不超过 32K Token

---

## 7. 版本日志

| 版本 | 日期 | 变更说明 |
|------|------|---------|
| 1.0 | 2026-04-05 | 初始版本，定义基础架构与指令集 |
