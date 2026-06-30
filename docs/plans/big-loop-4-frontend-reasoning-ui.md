# Big-Loop #4 — 前端接通:让推理能力页面可见

> 4A-BDD-TDD 大循环 #4。**价值标尺(操作侧定)**:让 Loop #1/#2/#3 已建的后端推理能力
> (本体树 / 实体图谱 / 一致性稽核)对终端用户**页面可见**——不是"API 返回 200",
> 而是真实用户能导航到页面、看到结果(§1: done = real user 可见)。
>
> **范围说明**:本 loop 纯前端 + api.ts(§4 frontend 全自主)。后端端点已 LIVE 验证,不动。
>
> **merge 边界**:3-loop autonomy mandate("完成3次")已终止。本 loop 默认回到
> §0 红线 #1——**停在未合并**,merge 由操作侧定。

---

## Gate-1:4A 架构

### Business
- **问题**:Loop #1/#2/#3 交付了 `/ontology`、`/entity-graph`、`/consistency` 三个后端端点,
  但 `frontend/src/lib/api.ts` **一个都没调用**。终端用户用前端**看不到**本体树、实体图谱、
  矛盾稽核——后端推理能力"works-but-hollow"(Gate-2 体验层不及格)。
- **价值**:把"后端已验证"变成"用户可用"。这是把已建能力兑现为体验的必要补完。

### Application
新增三个页面 + 一个 api.ts 扩展 + 导航入口,全部沿用现有模式(`'use client'` + `useEffect` fetch + CSS 变量主题 + `glass-card`):
1. **`/ontology` 页**——本体树可视化(Loop #1)。展示 `ontology_tree` 嵌套结构 + 节点数 + 定义。
2. **`/entity-graph` 页**——实体邻居图谱(Loop #2)。输入术语 + 深度 → 展示多跳邻居 + 关系边。
3. **`/consistency` 页**——一致性稽核面板(Loop #3)。展示已知矛盾列表 + "触发稽核"按钮(POST)+ 候选对数。
4. **`api.ts` 扩展**——`fetchOntology` / `fetchEntityGraph(term,depth)` / `fetchConsistency()` / `triggerConsistencyCheck()` + 类型。
5. **NavBar**——加 3 个导航入口。

### Data
- 复用现有后端响应形状(已 LIVE 验证):
  - `/ontology` → `{ontology_tree: [{term, parent, definition, children:[...]}], total_nodes, last_updated}`
  - `/entity-graph?term=&depth=` → `{term, depth, neighbors: [...], edges: [...], total_edges}`
  - `/consistency` GET → `{status, total, candidates_checked, last_updated, contradictions: [...]}`
  - `/consistency` POST → 同上(触发稽核后)
- **不新增后端产物,不改数据形状。**

### Technology
- Next.js **16.2.2** + React **19.2.4**(破坏性版本,但现有页已用 App Router Client Components 跑通,沿用同一模式,不引新范式)。
- 现有可视化先例:`graph/page.tsx` + `KnowledgeGraph.tsx`(SVG 力导图)。实体图谱可复用同类 SVG 思路或简化为列表+关系。
- 测试:Jest 单测(api.ts 类型/纯函数)+ Playwright e2e(页面可见)。无后端改动 → 不动 pytest。

### ADR
- **ADR-11:三个新页面各自独立路由**(`/ontology`、`/entity-graph`、`/consistency`),
  而非合并到一个"推理"超级页。理由:职责清晰、导航直达、与现有 `/graph`、`/qa` 单页模式一致。
- **ADR-12:api.ts 先行 + 类型化**,页面只消费。理由:与现有 `fetchGraph`/`searchSync` 模式一致,
  便于单测 + 后续 `app/` 后端切换时只改 api.ts。
- **ADR-13:实体图谱用"术语-邻居列表 + 关系边表格"**而非完整力导图。
  理由:实体图是 term↔term(非 doc↔doc),力导图过重;列表+表格对"看懂谁依赖谁"更直接。
  (若后续要力导图,复用 KnowledgeGraph 组件即可——不锁死。)
- **ADR-14:矛盾为 0 时显式展示"库内一致,无矛盾"**,不显示空列表误导。
  理由:诚实——当前 contradictions total=0 是有效结果,UI 要如实呈现。

### NFR
- 页面首屏 < 1s(后端本地,数据小)。
- 复用 CSS 变量主题,不引新依赖(零新 npm 包)。
- `npm run lint` 0 error,`npm run build` 通过。

---

## Gate-2:BDD UAT(页面可见预期)

| ID | 场景 | 可见预期(页面级,非 API) |
|---|---|---|
| F-1 | 用户点导航"本体" → /ontology 页 | 看到本体树嵌套结构 + 节点总数 > 0 + 最后更新时间 |
| F-2 | 用户点"实体图谱" → /entity-graph,输入"5G技术" + 深度2 | 看到 ≥1 个邻居术语 + 关系边列表(depends_on/part_of…) |
| F-3 | 用户点"一致性" → /consistency 页 | 看到候选对数(20)+ 矛盾数(0)+ "库内一致"诚实提示 |
| F-4 | 用户在 /consistency 点"触发稽核" | 按钮变 loading → 结果刷新(候选数/矛盾数更新) |
| F-5 | 后端某端点失败(断网/500) | 页面显示 ⚠ 错误提示,不白屏崩溃 |
| F-6 | NavBar 显示 3 个新入口且当前页高亮 | 导航可见、active 态正确 |

**UAT 元评审**:F-1/F-2/F-3 是"三能力各自可见"(核心);F-4 是交互闭环;F-5 是降级;F-6 是可达性。
覆盖"页面可见 + 不误报 + 不崩"。**价值层自检**:三个页面是否真的让推理能力"落地"?
是——本体可浏览、实体可探索、矛盾可稽核,从"API 200"升级到"用户可操作"。

### DoD
- `npm run lint` 0 error + `npm run build` 通过;
- Jest 单测绿(api.ts 类型 + 纯函数);
- **LIVE**:浏览器实开三个页面,截图/快照证明页面可见(F-1/F-2/F-3 实证,非 API 断言);
- 多视角评审过;**停在未合并**(3-loop mandate 已尽,merge 待操作侧)。
