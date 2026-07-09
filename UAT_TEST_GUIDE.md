# Karpathy-Style 知识库系统：自动化测试与 UAT 验收指南

本文档旨在为开发人员、测试人员及最终用户说明如何启动、运行和验证该知识库系统的各项能力。整个系统的构建严格遵照了 TDD (测试驱动开发) 理念，并在真实领域数据上通过了 UAT 验收。

## 一、 测试启动说明 🚀

### 1. 基础环境准备
确保您已正确安装 Python 3.10+，并已安装所需的核心包及测试支持库。
```bash
# 进入项目根目录
cd d:\administrator\Desktop\大模型产品化\知识库研究

# 安装生产与测试依赖
pip install -r requirements.txt
pip install pytest
```

### 2. 环境变量配置
系统的 TDD 测试框架（`pytest`）使用Mock技术完全离线运行，**无需** API Key 即可进行单元测试。
但在进行真实调用（如下文的 UAT 测试）时，根目录下必须具有合法的 `.env` 文件：
```ini
OPENAI_API_KEY=sk-xxxxxx
# 使用阿里百炼时的自定义地址
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

COMPILE_MODEL=qwen3.6-plus
ONTOLOGY_MODEL=qwen3.6-plus
RELATE_MODEL=qwen3.6-plus
SEARCH_MODEL=qwen3.6-plus
```

### 3. 运行 TDD 自动化单元测试
我们的 `tests/` 目录中共有 4 个主要测试模块，全面覆盖核心脚本逻辑（共 67 个 Test Cases）。
```bash
# 运行完整的自动化测试栈，并输出详细信息
D:\ProgramData\anaconda3\python.exe -m pytest tests/ -v
```
> **测试沙盒保护机制**：`conftest.py` 配置了自动目录注入，测试框架将在 `tmp_path` 下自动构建虚拟文件树（临时隔离的 `wiki/`, `meta/`, `raw/` 等目录），**完全不会**影响或污染生产知识库数据，请放心运行。

---

## 二、 UAT 用户验收测试用例 📋

在真实环境/生产环境中，我们可以通过下列 UAT 黑盒测试用例来验证项目四大核心模块能否跑通。系统已预先在 `originals/` 中内置了三份完整的真实场景文档用于测试校验。

### UAT-01: 多格式文档的解析与摄入 (Ingest)
*   **测试目的**：验证系统能否将任意排版风格的非结构化文件标准化摄入。
*   **前置条件**：`originals/` 目录中已放置测试文件 `01_port_auto_spec.md`。
*   **执行步骤**：
    1. 运行命令 `D:\ProgramData\anaconda3\python.exe scripts/ingest.py`。
*   **预期结果**：
    *   在控制台输出“处理文件: 01_port_auto_spec.md，成功摄入”。
    *   `raw/` 目录下新增对应 SHA256 唯一生成的文档 `.txt` 文件与对应的 `.meta.yaml`。
    *   `meta.yaml` 状态初始化为 `status: raw`。

### UAT-02: LLM 无向量编译与知识提取 (Compile)
*   **测试目的**：验证 LLM Agent 能否充当“编译器”，基于全文生成高密度摘要并识别本体节点。
*   **前置条件**：前序 UAT-01 执行成功，且 `.env` 配置合法。
*   **执行步骤**：
    1. 运行命令 `D:\ProgramData\anaconda3\python.exe scripts/compile.py`。
*   **预期结果**：
    *   根据文档内容生成 `xxx.summary.yaml`。
    *   LLM 成功抽取出强业务语义的关键词（如 `5G技术`、`控制信令延迟` 等）并写入 `xxx.ontology.yaml`。
    *   全局文件 `wiki/index.yaml` 以及 `meta/ontology/global_ontology.yaml` 更新。
    *   该文档的 `.meta.yaml` 状态自动流转为 `compiled`。

### UAT-03: 三层渐进式降维检索体验 (Search)
*   **测试目的**：验证无向量环境下的 "Context Stuffing" 长下文直接检索与引用溯源能力。
*   **前置条件**：至少三份文档已被成功 Compile。
*   **执行步骤**：
    1. 执行命令 `D:\ProgramData\anaconda3\python.exe scripts/search.py "岸桥 远控 网络 延迟"`。
*   **预期结果**：
    *   **Layer 1**：基于 BM25 的标点/空格分词，快速筛选出候选文档（耗时应极短）。
    *   **Layer 2**：LLM 对候选集进行去伪存真，精准剔除仅有字面匹配但上下文无关的文档。
    *   **Layer 3**：LLM 通过直接读取 Layer 2 中胜出文档的 Raw Text，推理生成最终答案，并在答案末尾带有严格的 Markdown 格式引用（格式：`[文档标题 · 章节]`）。

### UAT-04: 全局语义知识关联与重组 (Relate)
*   **测试目的**：验证系统的跨文本推荐以及图谱边建立的功能。
*   **前置条件**：多份知识重叠的文档均被成功 Compile。
*   **执行步骤**：
    1. 执行命令对某文档强制进行图谱关联推荐：`D:\ProgramData\anaconda3\python.exe scripts/relate.py --recommend <特定doc_id>`。
*   **预期结果**：
    *   终端正确吐出树状关联结构。
    *   列出关系类别（如 `同主题`，`补充说明`），并给出该关联的置信度百分比。
    *   LLM 生成的人类可读的原因解释（如“两者均围绕港口作业展开，一侧重垂直作业，一侧重水平作业”）。
    *   `meta/relations/knowledge_graph.yaml` 更新并长期固化该关联网络（Edges）。
