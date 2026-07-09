"""
scripts/compile.py — LLM 编译脚本
对已摄入（status=raw）的文档进行 LLM 编译：
  Step A: 生成结构化摘要 (wiki/{doc_id}.summary.yaml)
  Step B: 抽取本体关键词 (meta/ontology/{doc_id}.ontology.yaml)
  Step C: 更新全局索引 (wiki/index.yaml)
  Step D: 触发关系检测 (调用 relate.py)

用法:
  python scripts/compile.py              # 编译所有 status=raw 的文档
  python scripts/compile.py doc_id      # 编译指定文档

依赖环境变量（在 .env 文件中配置）:
  OPENAI_API_KEY      — OpenAI API Key
  OPENAI_BASE_URL     — 可选，用于 API 代理或本地兼容服务
  COMPILE_MODEL       — 摘要编译模型，默认 gpt-4o
  ONTOLOGY_MODEL      — 本体抽取模型，默认 gpt-4o-mini
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

# ─── 路径配置 ───────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.parent
RAW_DIR = BASE_DIR / "raw"
WIKI_DIR = BASE_DIR / "wiki"
META_DIR = BASE_DIR / "meta"
ONTOLOGY_DIR = META_DIR / "ontology"
INDEX_FILE = WIKI_DIR / "index.yaml"
GLOBAL_ONTOLOGY_FILE = ONTOLOGY_DIR / "global_ontology.yaml"

TZ_CST = timezone(timedelta(hours=8))


# ─── LLM 客户端 ──────────────────────────────────────────────────────────────

def _load_env():
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

def get_llm_client():
    """初始化 OpenAI 客户端，支持自定义 base_url"""
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("请安装 openai: pip install openai")

    _load_env()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "未找到 OPENAI_API_KEY。请在 知识库研究/.env 中配置:\n"
            "OPENAI_API_KEY=sk-..."
        )
    base_url = os.environ.get("OPENAI_BASE_URL")
    return OpenAI(api_key=api_key, **({"base_url": base_url} if base_url else {}))


def llm_call(client, model: str, system: str, user: str,
             retries: int = 3) -> dict:
    """调用 LLM 并解析 JSON 响应，内置重试逻辑"""
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.2,
            )
            return json.loads(resp.choices[0].message.content)
        except json.JSONDecodeError:
            print(f"  [WARN] JSON 解析失败，重试 {attempt+1}/{retries}...")
            time.sleep(5 * (attempt + 1))
        except Exception as e:
            if attempt == retries - 1:
                raise
            print(f"  [WARN] API 错误: {e}，重试 {attempt+1}/{retries}...")
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("LLM 调用失败，已达最大重试次数")


# ─── Step A：摘要编译 ────────────────────────────────────────────────────────

SUMMARY_SYSTEM = """你是一个技术文档情报分析师，专注于港口智慧化与数字化转型领域。
请以 JSON 格式输出结构化摘要，严格遵守 schema，不要输出 schema 以外的字段。
所有文本字段使用文档的原始语言（中文文档用中文，英文文档用英文）。"""

SUMMARY_SCHEMA = """{
  "abstract": "200-500字的结构化摘要，涵盖文档核心论点和主要结论",
  "key_points": ["核心论点1", "核心论点2"],
  "sections": [
    {"title": "章节标题", "summary": "章节摘要（50-100字）", "page_range": "起始页-结束页"}
  ],
  "document_type": "technical_spec|research_paper|report|standard|meeting_minutes|other",
  "writing_style": {
    "tone": "formal-technical|informal|academic|policy|other",
    "typical_patterns": ["典型句式1", "典型句式2"],
    "key_terminology": {"术语": 出现次数}
  }
}"""


def compile_summary(client, doc_id: str, text: str, model: str) -> dict:
    print(f"  [Step A] 生成结构化摘要 (模型: {model})...")
    # 长文档截断（保留前 6W 字符，约对应 40K token）
    truncated = text[:60000]
    if len(text) > 60000:
        print(f"  [WARN] 文档过长，截断至 60000 字符（原始 {len(text)}）")

    user_prompt = f"请分析以下文档，输出符合 schema 的结构化摘要。\n\nJSON Schema:\n{SUMMARY_SCHEMA}\n\n文档内容:\n{truncated}"
    result = llm_call(client, model, SUMMARY_SYSTEM, user_prompt)

    # 写入 wiki/
    WIKI_DIR.mkdir(exist_ok=True)
    summary_data = {
        "doc_id": doc_id,
        "compiled_at": datetime.now(TZ_CST).isoformat(),
        **result,
    }
    out_path = WIKI_DIR / f"{doc_id}.summary.yaml"
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(summary_data, f, allow_unicode=True, sort_keys=False)
    print(f"  [OK] 摘要已写入: {out_path.name}")
    return summary_data


# ─── Step B：本体抽取 ────────────────────────────────────────────────────────

ONTOLOGY_SYSTEM = """你是一个领域知识本体工程师。请从文档中抽取专业术语并构建本体树节点。
参考现有本体进行归类，若无合适父类则创建新父类（最多追溯2层）。
confidence < 0.75 的术语请不要输出。输出 JSON，严格遵守 schema。"""

ONTOLOGY_SCHEMA = """{
  "ontology_nodes": [
    {
      "term": "专业术语",
      "parent": "直接父类（必填）",
      "grandparent": "祖父类（可选，若父类已存在于本体树则不需要）",
      "definition": "在本文档上下文中的定义（50字以内）",
      "is_new_node": true
    }
  ]
}"""


def compile_ontology(client, doc_id: str, text: str,
                     summary: dict, model: str) -> dict:
    print(f"  [Step B] 抽取本体关键词 (模型: {model})...")

    # 读取现有全局本体（简化版，只取 term 列表）
    existing_terms = _get_existing_terms()
    ontology_hint = "现有本体术语（优先归类）: " + ", ".join(existing_terms[:50])

    # 使用摘要+关键词作为输入（节省 token）
    doc_context = (
        f"文档摘要: {summary.get('abstract', '')}\n"
        f"核心论点: {'; '.join(summary.get('key_points', []))}"
    )

    user_prompt = (
        f"{ontology_hint}\n\n{doc_context}\n\n"
        f"JSON Schema:\n{ONTOLOGY_SCHEMA}"
    )
    result = llm_call(client, model, ONTOLOGY_SYSTEM, user_prompt)

    # 写入 meta/ontology/
    ONTOLOGY_DIR.mkdir(parents=True, exist_ok=True)
    ontology_data = {
        "doc_id": doc_id,
        "extracted_at": datetime.now(TZ_CST).isoformat(),
        **result,
    }
    out_path = ONTOLOGY_DIR / f"{doc_id}.ontology.yaml"
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(ontology_data, f, allow_unicode=True, sort_keys=False)

    # 更新全局本体
    _update_global_ontology(result.get("ontology_nodes", []))
    print(f"  [OK] 本体已写入: {out_path.name}")
    return ontology_data


def _get_existing_terms() -> list[str]:
    """从全局本体树中提取所有 term"""
    if not GLOBAL_ONTOLOGY_FILE.exists():
        return []
    with open(GLOBAL_ONTOLOGY_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    terms = []
    def _collect(nodes):
        for node in nodes:
            terms.append(node.get("term", ""))
            _collect(node.get("children", []))
    _collect(data.get("ontology_tree", []))
    return [t for t in terms if t]


def _update_global_ontology(new_nodes: list):
    """将新节点合并到全局本体为**真树**(消除顶层孤儿)。

    Big-Loop #1 修正:旧实现把每个新节点都 extend 到 ontology_tree 顶层,
    只留 parent 标签但不真正挂到父节点下 → 产生大量顶层孤儿与悬空 parent 引用。
    现改用 scripts.ontology.merge_ontology_nodes 做真树插入(parent 在树中
    则挂其 children;parent 缺失但 grandparent 在则建中间父节点)。
    """
    if not GLOBAL_ONTOLOGY_FILE.exists():
        return
    with open(GLOBAL_ONTOLOGY_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    from scripts.ontology import merge_ontology_nodes
    tree = data.setdefault("ontology_tree", [])
    added = merge_ontology_nodes(tree, new_nodes)

    if added:
        data["last_updated"] = datetime.now(TZ_CST).isoformat()
        data["total_nodes"] = data.get("total_nodes", 0) + added
        with open(GLOBAL_ONTOLOGY_FILE, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False)
        print(f"  [OK] 全局本体新增 {added} 个节点(真树合并)")


# ─── Step C：更新全局索引 ────────────────────────────────────────────────────

def update_index(doc_id: str, meta: dict, summary: dict, ontology: dict):
    print("  [Step C] 更新全局索引...")
    index = _load_yaml(INDEX_FILE, {"documents": []})

    # 移除旧记录（重新编译时）
    index["documents"] = [d for d in index["documents"] if d["id"] != doc_id]

    abstract = summary.get("abstract", "")
    abstract_short = abstract[:100] + ("..." if len(abstract) > 100 else "")
    ontology_terms = [n["term"] for n in ontology.get("ontology_nodes", [])]

    index["documents"].append({
        "id": doc_id,
        "title": meta.get("title", doc_id),
        "ingested_at": meta.get("ingested_at", ""),
        "source_type": meta.get("source_type", ""),
        "abstract_short": abstract_short,
        "ontology_terms": ontology_terms,
        "document_type": summary.get("document_type", "other"),
        "file_hash": meta.get("file_hash", ""),
    })

    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        yaml.dump(index, f, allow_unicode=True, sort_keys=False)
    print(f"  [OK] 索引已更新，当前共 {len(index['documents'])} 篇文档")


# ─── 辅助函数 ────────────────────────────────────────────────────────────────

def _load_yaml(path: Path, default) -> dict:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or default
    return default


def _update_meta_status(doc_id: str, status: str, error: str = ""):
    meta_path = RAW_DIR / f"{doc_id}.meta.yaml"
    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = yaml.safe_load(f) or {}
        meta["status"] = status
        meta["error_message"] = error
        with open(meta_path, "w", encoding="utf-8") as f:
            yaml.dump(meta, f, allow_unicode=True, sort_keys=False)


def get_raw_doc_ids(filter_status: str = "raw") -> list[str]:
    """获取指定状态的所有文档 ID"""
    if not RAW_DIR.exists():
        return []
    ids = []
    for meta_file in sorted(RAW_DIR.glob("*.meta.yaml")):
        with open(meta_file, "r", encoding="utf-8") as f:
            meta = yaml.safe_load(f) or {}
        if meta.get("status") == filter_status:
            ids.append(meta["id"])
    return ids


# ─── 核心编译流程 ────────────────────────────────────────────────────────────

def compile_doc(doc_id: str, client, compile_model: str,
                ontology_model: str) -> bool:
    """编译单个文档，返回是否成功"""
    print(f"\n{'='*50}")
    print(f"[COMPILE] 开始编译: {doc_id}")

    # 读取原始文本
    txt_path = RAW_DIR / f"{doc_id}.txt"
    meta_path = RAW_DIR / f"{doc_id}.meta.yaml"
    if not txt_path.exists():
        print(f"  [ERROR] 找不到原始文本: {txt_path}")
        return False

    text = txt_path.read_text(encoding="utf-8")
    meta = _load_yaml(meta_path, {})

    # 标记为编译中
    _update_meta_status(doc_id, "compiling")

    try:
        # Step A
        summary = compile_summary(client, doc_id, text, compile_model)
        # Step B
        ontology = compile_ontology(client, doc_id, text, summary, ontology_model)
        # Step C
        update_index(doc_id, meta, summary, ontology)
        # Step D — 触发关系检测
        print("  [Step D] 触发关系检测...")
        _run_relate(doc_id)

        _update_meta_status(doc_id, "compiled")
        print(f"[DONE] {doc_id} 编译完成 ✅")
        return True

    except Exception as e:
        print(f"[ERROR] {doc_id} 编译失败: {e}")
        _update_meta_status(doc_id, "error", str(e))
        return False


def _run_relate(doc_id: str):
    """调用 relate.py 进行关系检测（子进程方式，避免循环依赖）"""
    import subprocess
    relate_script = Path(__file__).parent / "relate.py"
    result = subprocess.run(
        [sys.executable, str(relate_script), doc_id],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"  [WARN] 关系检测失败: {result.stderr[:200]}")
    else:
        # 打印 relate.py 的输出
        for line in result.stdout.strip().splitlines():
            print(f"  {line}")


# ─── 入口 ────────────────────────────────────────────────────────────────────

def main():
    _load_env()
    compile_model = os.environ.get("COMPILE_MODEL", "gpt-4o")
    ontology_model = os.environ.get("ONTOLOGY_MODEL", "gpt-4o-mini")

    client = get_llm_client()

    if len(sys.argv) > 1:
        doc_ids = sys.argv[1:]
    else:
        doc_ids = get_raw_doc_ids("raw")
        if not doc_ids:
            print("没有待编译的文档（status=raw）。请先运行 ingest.py。")
            return

    print(f"待编译文档数: {len(doc_ids)}")
    success, failed = 0, 0
    for doc_id in doc_ids:
        if compile_doc(doc_id, client, compile_model, ontology_model):
            success += 1
        else:
            failed += 1

    print(f"\n{'='*50}")
    print(f"✅ 编译完成: 成功 {success} 篇，失败 {failed} 篇")
    if failed:
        print("💡 失败文档可通过 python scripts/compile.py <doc_id> 单独重试")


if __name__ == "__main__":
    main()
