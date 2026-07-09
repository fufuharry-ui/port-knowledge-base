# Big-Loop 系列交付总结(本体 + 推理,3 轮)

> 4A-BDD-TDD / Y-Model 大循环系列。价值标尺:让 Karpathy 零向量知识库的
> "本体构建 + 推理功能"在企业/个人办公场景**可见生效**。
> 授权:动核心 + 完整流程后 merge。3 轮全部完成,已合并 master。

---

## 1. 总览

| Loop | 主题 | commit | 核心能力 | LIVE 实证 |
|---|---|---|---|---|
| #1 | 本体真树 + 检索查询扩展 | `4da0a22` | 本体孤儿消除、Layer1 注入上位/兄弟词、/graph 边修复、/ontology 端点 | 69→9 真根;本体扩展词在查询生效 |
| #2 | 实体级知识图谱 | `44d9315` | term↔term 关系抽取、多跳双向邻居、查询注入实体邻居 | 9/9 文档 30 边;/entity-graph 5 术语非空邻居 |
| #3 | 跨文档一致性推理 | `91ad3e0` | 矛盾候选对纯逻辑、LLM 矛盾判定、Layer3 ⚠️ 提示、/consistency API | 20 候选对稽核→0 矛盾(库内部一致) |

**测试**:125 → **187 passed**(+62),零回归。**零向量哲学保持**(未引外部向量库)。

---

## 2. 各 Loop 的 4A 闸门状态

### Loop #1 — 本体真树
- **Business**:本体是"分类的脊椎",但历史抽取产生 66 个孤儿顶层节点(父引用悬空)→ 本体不可查、检索不受益。
- **Application**:`merge_ontology_nodes`(真树插入,非扁平 append)+ `expand_query_with_ontology`(Layer1 注入上位/兄弟)+ `rebuild_tree_from_nodes`(注册表式拓扑重建,消除顺序依赖)+ /ontology 端点。
- **ADR**:ADR-1 真树合并、ADR-2 查询扩展仅注入 len≥2 词、ADR-3 重建用注册表拓扑、ADR-4 通信技术缺祖父时升为根。
- **Gate**:U-1~U-6 全过;P0 评审发现 /qa、/search/stream 未传 ontology → 已修复 + P0 回归守卫。

### Loop #2 — 实体级 KG
- **Business**:文档级关系(谁和谁同主题)粒度太粗;用户问"5G专网"时,系统不知道"岸桥远控 依赖 5G专网"这种**实体级语义关系**。
- **Application**:`extract_entity_relations`(单文档内 term→term 关系抽取)+ `get_entity_neighbors`(多跳双向 BFS)+ `expand_query_with_entities`(查询注入邻居)+ /entity-graph 端点。
- **ADR**:ADR-5 实体关系独立于文档级 KG、ADR-6 关系类型白名单(depends_on/part_of/supports/alternative_of)+ conf≥0.70、ADR-7 查询扩展同时注入分类词(#1)与语义邻居(#2)去重。
- **Gate**:E-1~E-5 全过;LIVE 30 边实证。

### Loop #3 — 一致性推理
- **Business**:#1/#2 让本体可查、实体可推理,但**内部一致性无人把关**——多文档可能对同一指标/论断冲突,用户读到矛盾答案不知信谁。
- **Application**:`find_contradiction_candidates`(纯逻辑,KG 强关联边 + 共享实体术语→候选对)+ `detect_contradiction`(LLM 判定 + 推理链)+ Layer3 ⚠️ 提示(附加非拦截)+ /consistency GET/POST。
- **ADR**:ADR-8 独立模块(不混入 relate)、ADR-9 候选对纯函数(无 IO 可单测)、ADR-10 提示附加非拦截(版本迭代由用户判断)。
- **Gate**:C-1~C-5 全过;P0 守卫防止 contradictions 在 /qa 断线;LIVE 20 对稽核管道端到端跑通。

---

## 3. 关键工程约束(已守,后续须守)

1. **零向量哲学**:本体/KG/一致性全是 Context Stuffing 强化,不引外部向量库。embedding 仅 boost BM25,非替代架构。
2. **测试隔离**:monkeypatch 模块常量 → 任何新路径常量必须同步进 `tests/conftest.py` 的 `patch_*_paths` fixture。
3. **P0 主路径守卫**:`/qa`、`/search/stream`(前端主路径)调用 layer 函数时**必须显式传全参数**(ontology、contradictions…)。Loop #1 曾因漏传 ontology 导致功能静默失效——此后每个新参数加 P0 回归测试。
4. **降级优先**:ontology/vector/contradictions/entity 缺失 → 降级(无提示/纯 BM25),绝不崩。
5. **前端用 `api/main.py`**(非 `app/`);`scripts/` 纯函数无文件 IO 便于隔离测试。

---

## 4. 已知边界与后续方向(诚实标注)

- **前端未接 /consistency**:`frontend/src/lib/api.ts` 尚无 /consistency 与 /entity-graph 调用。Loop #3 的矛盾提示通过 SSE delta 已在回答区**可见**,但无独立稽核面板。计划标注为"最小:回答区提示,不强求新页面"——非缺陷,是范围控制。
- **一致性稽核的 LLM 成本**:全库 20 候选对 × 1 LLM 调用。文档量增大后需批量化或增量稽核(仅新入库文档触发)。
- **实体关系抽取的单文档局限**:`extract_entity_relations` 仅在单文档内抽 term→term;跨文档的实体关系(如 doc_A 的实体 与 doc_B 的实体)未抽取——当前靠共享实体术语在检索时隐式关联。

---

## 5. 验证证据(非断言)

- `pytest tests/ -q` → **187 passed**(2026-06-29,master)。
- LIVE: `relate --rebuild-all` → 9/9 文档,30 实体边。
- LIVE: `/entity-graph?term=5G技术&depth=2` → 4 邻居(含 eMBB/uRLLC 兄弟)。
- LIVE: `run_consistency_check` → candidates_checked=20,total=0(库内部一致)。
- git:`master` = `91ad3e0`,fast-forward 自 dev,无偏离。
