# Big-Loop #3 — 一致性推理(跨文档矛盾检出与推理链)

> 4A-BDD-TDD 大循环 #3(系列终章)。价值标尺:当知识库内文档对同一事实
> (如"远控端到端延迟要求")给出冲突论断时,系统能**检出矛盾**并给出
> **推理链**(文档 A 说 X,文档 B 说 Y,冲突点=…)。授权:动核心 + 完整流程后 merge。

---

## Gate-1:4A 架构

### Business
- **问题**:#1/#2 让本体可查、实体可推理,但知识库**内部一致性**无人把关——
  多文档可能对同一指标/论断有冲突(标准更新、不同来源),用户读到矛盾答案不知该信谁。
- **价值**:从"检索+关系"升级到"推理+稽核"——知识库自证可信。

### Application
1. **矛盾检测器**(`scripts/consistency.py` 纯逻辑骨架 + LLM 判定):
   输入一组文档(同主题/共享实体邻居),LLM 判定是否存在事实性矛盾,输出矛盾对 + 推理链。
   产物:`meta/consistency/contradictions.yaml`。
2. **检索时矛盾提示**(`scripts/search.py` Layer3):回答时若 Top 文档间存在已知矛盾,
   在回答中附加"⚠️ 知识库内存在不一致"提示 + 推理链(让用户知情,不静默给单一答案)。
3. **API**:`POST /api/v1/consistency` 触发全库稽核;`GET /api/v1/consistency` 查看结果。
4. **前端**:矛盾在图谱/回答中以徽章可见(最小:回答区提示;不强求新页面)。

### Data
- 新产物:`meta/consistency/contradictions.yaml`(pairs: doc_a/doc_b/conflict_point/reasoning_chain/confidence)。
- 复用:`knowledge_graph.yaml`(文档级关系,same_topic 对作矛盾检测候选)、`entity_relations.yaml`(#2)。
- **不引向量库**。

### Technology
- 候选对生成:纯逻辑(从 KG 的 same_topic/supplements 边 + 共享实体术语推导)。
- 矛盾判定:LLM(SEARCH_MODEL,复用),单次/批。
- 测试:纯逻辑(候选对生成)无 fixture;LLM 判定 mock。

### ADR
- **ADR-8:矛盾检测独立模块 `scripts/consistency.py`**,不混入 relate.py(职责不同:
  relate 是关联,consistency 是稽核;混入会膨胀 relate 测试)。
- **ADR-9:候选对生成纯函数 `find_contradiction_candidates(kg_edges, entity_relations)`**,
  无文件 IO,可隔离单测。
- **ADR-10:矛盾提示是"附加"非"拦截"**——Layer3 仍正常回答,矛盾作为来源后的提示块。
  理由:矛盾可能是版本迭代(非错误),由用户判断,不阻断检索。

---

## Gate-2:BDD UAT

| ID | 场景 | 可见预期 |
|---|---|---|
| C-1 | 两文档 same_topic + 共享实体术语 → 生成矛盾检测候选对 | 纯测:候选对含该对 |
| C-2 | LLM 判定两文档冲突 → contradictions.yaml 记录 + 推理链 | 集成测(mock LLM) |
| C-3 | 检索回答时,若 Top 文档有已知矛盾 → 回答附"⚠️ 不一致"提示 | 集成测 |
| C-4 | 无矛盾 → 回答无提示(不误报) | 集测 |
| C-5 | `POST /api/v1/consistency` 触发稽核返回报告 | api 测 |

**UAT 元评审**:C-1/C-2 是矛盾检出核心;C-3/C-4 是"用户可见且不误报";C-5 铺 API。范围严格。

### DoD
- pytest 全绿且 ≥167+新测;LIVE 实证一次真实稽核(或降级路径可见);
- 3 个 loop 完成,系列交付总结;停 dev 未 merge → merge。
