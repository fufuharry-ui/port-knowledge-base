# Big-Loop #1 — 本体树正确性 + 检索真正用本体

> 4A-BDD-TDD / Y-Model 大循环 #1 的 Gate-1(架构)+ Gate-2(验收)产物。
> 价值标尺(操作员确认):用户提问时**能看见本体在被使用**;前端图谱显示**真实连线**。
> 授权:操作员授权直接动 `scripts/` 核心 + 走完完整流程后可 merge。

---

## Gate-1:4A 架构

### Business(业务)
- **问题**:本体树合并断裂(70+ 孤儿节点悬空)、检索从不读本体/图谱 → "推理"缺失、`/graph` 返回 0 边导致前端图谱无连线。
- **价值**:让"关系即知识/编译优于检索"哲学在查询时**第一次真正生效**,且用户可见。

### Application(应用)— 三件事,边界严格
1. **本体树合并修复**(`scripts/compile.py`):`_update_global_ontology` 改为真树插入——全树搜 parent,找到则挂其下;parent 缺失但 grandparent 存在则建中间父节点再挂;都缺则 parent 作新根。消除顶层孤儿。
2. **本体查询扩展 Layer 1.5**(新 `scripts/ontology.py` 纯逻辑 + `scripts/search.py` 接入):查询时识别命中的本体术语,向 BM25 注入其**兄弟/上位词**作为加分项,捞出用户字面词 miss 的相关文档。本体缺失 → 优雅降级为纯 BM25(零向量哲学不变)。
3. **`/graph` 边修复 + `/ontology` 端点**(`api/main.py`,前端对接层):修 per-doc 字段 `target_doc_id` 与 KG 键 `edges` 的读取;新增 `GET /api/v1/ontology`。

### Data(数据)
- 无新数据模型。复用现有 YAML:`global_ontology.yaml`(修复为真树)、`knowledge_graph.yaml`、per-doc `*.relations.yaml`。
- **不引入向量库**(零向量护栏)。

### Technology(技术)
- 纯 Python,无新依赖。`jieba` 已在 requirements。
- **测试隔离红线**:`scripts/search.py` 新增 `GLOBAL_ONTOLOGY_FILE` 模块常量 → 必须同步加进 [conftest.py `patch_search_paths`](../../tests/conftest.py#L88)。`scripts/ontology.py` 设计为**纯函数(入参传 tree,无文件 IO)**,故无需新 path fixture、可独立单测。

### 关键 ADR
- **ADR-1:本体逻辑放新纯函数模块 `ontology.py`,文件 IO 留在 search.py**。理由:测试隔离靠 monkeypatch 模块常量,纯函数无 path 常量 → 不破坏隔离,且单测无需 fixture。
- **ADR-2:本体缺失/为空 → 降级纯 BM25**。理由:对齐 `EmbeddingClient` 既有降级模式;测试绝不依赖外部 API。
- **ADR-3:`layer1_filter(query, index, top_k, ontology=None)`**——`ontology=None` 时行为与旧版**完全一致**(现有 4 个 Layer1 测试不破)。
- **ADR-4:修旧测试断言属"修正非削弱"**——旧 `test_global_ontology_updated_with_new_nodes` 断言 `+2` 实为对 bug 行为的编码;真树合并下 SAMPLE 增 `+3`(岸桥远控挂 港口自动化;通信技术作 基础设施 子节点、5G专网挂其下)。将断言改为 `+3` 并**新增嵌套结构断言**。诚实记录于此。

---

## Gate-2:BDD UAT(页面可见预期,非仅 API 200)

| ID | 场景(给定/当/那么) | 可见预期 |
|---|---|---|
| U-1 | 新文档抽取 term(parent 已存在树中)→ 编译合并 | `global_ontology.yaml` 中该 term **嵌套在 parent.children 下**,非顶层孤儿(单测+产物核对) |
| U-2 | term 的 parent 不存在但 grandparent 存在 → 合并 | 中间 parent 被建在 grandparent 下,term 挂其下;`total_nodes` 反映真实增量 |
| U-3 | 用户查"岸桥远控",知识库有相关文档但仅标了上位/兄弟术语(如"港口自动化""场桥远控") | 本体扩展使这些文档**进入候选**(单测:扩展集含上位/兄弟) |
| U-4 | 知识库无 global_ontology 文件 → 查询 | **降级纯 BM25**,不报错(等价旧行为) |
| U-5 | 打开前端 `/graph` 页 | ECharts 显示**真实连线**(edge-count > 0),节点按关系类型着色 |
| U-6 | `GET /api/v1/ontology` | 返回完整本体树 JSON(供后续前端本体页;本 loop 不做前端页) |

**UAT 元评审(验标准本身)**:U-1~U-4 是编排层正确性(必须有);U-5 是用户可见价值锚(图谱连线);U-6 为 #2/#3 loop 铺路。未把"实体级 KG/矛盾推理"塞进来(范围纪律)。标准覆盖真业务链(编译→检索→图谱),非孤立端点。

### DoD(完成定义)
- 全套 pytest 绿且 **≥125+新测**(棘轮只升);新增本体合并/扩展/graph 边/ontology 端点测试。
- 0 lint 错误(后端无 linter 配置则跳过,不强造)。
- **LIVE 实证**:起 `api.main:app` + 前端,`/graph` 页可见连线;一次真实查询的 thought-trace 体现本体扩展(无 .env 则降级路径可见,如实标注)。
- 停在 **dev 分支未合并**,操作员 merge。
