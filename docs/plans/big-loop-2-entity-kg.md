# Big-Loop #2 — 实体级知识图谱 + 图谱增强检索

> 4A-BDD-TDD 大循环 #2。价值标尺:用户问"5G专网支撑哪些作业"时,系统能沿
> **实体级关系**(术语→术语,如"岸桥远控 依赖 5G专网")多跳推理,拉出关联文档。
> 授权:动核心 + 完整流程后可 merge。前置:#1 已 merge(master=4da0a22)。

---

## Gate-1:4A 架构

### Business
- **问题**:Loop #1 让本体成真树、检索用本体扩展(上位/兄弟),但术语间**无语义关系**
  (只是分类树)。用户问"X 支撑/依赖 Y"类问题,系统无实体级推理能力。
- **价值**:从"分类检索"升级到"关系推理"——知识图谱真正可查询、可多跳。

### Application
1. **实体关系抽取**(`scripts/ontology.py` 纯逻辑 + `scripts/relate.py` LLM 抽取):
   从 per-doc 本体节点 + 摘要,抽 term→term 关系(类型:depends_on/depends_on、part_of、supports、alternative_of)。
   产物:`meta/ontology/entity_relations.yaml`。
2. **图谱查询引擎**(`scripts/ontology.py` 纯函数):给定术语,返回其邻居(多跳,深度可配)。
3. **检索增强**(`scripts/search.py` Layer1.6):查询命中术语时,把其**实体邻居**也注入 BM25
   (区别于 #1 的上位/兄弟——这是语义关系邻居,如"依赖""支撑"的目标)。
4. **API**:`GET /api/v1/entity-graph?term=...` 返回术语邻居;前端图谱页可切换文档图/实体图。

### Data
- 新产物:`meta/ontology/entity_relations.yaml`(edges: source_term/target_term/type/confidence/evidence/doc_id)。
- 复用:`global_ontology.yaml`(#1 真树)、`wiki/index.yaml`。
- **不引向量库**。

### Technology
- 纯 Python + LLM(qwen-plus,RELATE_MODEL)。
- 实体关系抽取:LLM 单次调用(输入:某文档的 ontology_terms + 摘要;输出:该文档术语间关系)。
- 测试:纯逻辑函数无 path fixture;LLM 抽取用 mock_llm_client(同现有 relate 测试模式)。

### ADR
- **ADR-5:实体关系独立产物,不混入 knowledge_graph.yaml**。后者是文档级关系(已稳定);
  实体级是新维度,混入会破坏现有 relate 测试 + `/graph` 契约。
- **ADR-6:图谱查询纯函数 `get_entity_neighbors(tree, entity_relations, term, depth)`**,入参传数据,
  无文件 IO(同 ontology.py 既有风格,便于隔离测试)。
- **ADR-7:Layer1 扩展优先级**:本体扩展(上位/兄弟,#1)+ 实体邻居(#2)合并去重后注入;
  ontology=None 或无 entity_relations 时降级(向后兼容)。

---

## Gate-2:BDD UAT

| ID | 场景 | 可见预期 |
|---|---|---|
| E-1 | 文档编译后,其术语间关系被抽取 | entity_relations.yaml 含该文档的 term→term 边 |
| E-2 | 查"岸桥远控",其 depends_on 的"5G专网"邻居被注入检索 | 单测:扩展集含实体邻居 |
| E-3 | 查"5G专网",反向(supports 岸桥远控)也召回 | 单测:双向遍历 |
| E-4 | 无 entity_relations 文件 → 降级 | 不报错,等价 #1 行为 |
| E-5 | `GET /api/v1/entity-graph?term=5G专网` | 返回邻居 JSON(供前端) |

**UAT 元评审**:E-1~E-3 是实体推理核心;E-4 守降级;E-5 铺前端。范围严格——不做矛盾推理(留 #3)。

### DoD
- pytest 全绿且 ≥149+新测;LIVE 实证 entity-graph 端点 + 一次真实查询体现实体邻居;
- 停 dev 未 merge;完整流程+LIVE 后 merge。
