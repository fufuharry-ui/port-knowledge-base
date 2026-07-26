# CLAUDE.md

本文件定义 Claude Code 在本仓库中的开发边界、验证要求和协作流程。

## 核心原则

Claude Code 是实施和审查工具，不是项目负责人。所有修改必须来自明确任务合同或用户指令。

优先级：
1. 用户当前指令
2. 任务合同 `docs/dev/tasks/`
3. 本文件
4. `ANTIGRAVITY.md`
5. 测试与现有代码

没有明确任务范围时，只允许分析，不修改文件。

## 工作流程

每个任务必须具备：
- 问题描述
- 修改目标
- 非目标
- 允许修改文件
- 验收命令

禁止：
- 自行扩大范围
- 无关重构
- 自行改变技术路线
- 未授权 commit/push/merge/rebase
- 根据历史日志声称测试通过

完成声明必须包含：
- 实际修改文件
- 根因分析
- 验证命令及结果
- 未验证事项

## 架构边界

项目包含：
- `scripts/`：知识处理核心引擎
- `api/`：当前前端使用的 FastAPI 服务
- `app/`：模块化 FastAPI 服务，尚未完成统一
- `frontend/`：Next.js 应用

当前存在两个后端入口。涉及前端链路默认修改 `api/`；涉及 `app/` 必须由任务明确指定。不得自行同步两套后端。

## 数据安全

以下目录属于真实知识资产：
- `originals/`
- `raw/`
- `wiki/`
- `meta/`

测试不得污染真实数据，必须使用隔离目录。

涉及以下共享文件必须考虑并发和原子写：
- `wiki/index.yaml`
- `meta/ontology/global_ontology.yaml`
- `meta/relations/knowledge_graph.yaml`

## 测试要求

默认采用：

失败测试 → 最小修复 → 目标测试 → 回归测试 → diff检查

单元测试禁止：
- 真实 LLM API
- 真实 Embedding API
- 外部网络
- 用户真实数据

不要根据旧测试日志声称通过，必须运行当前验证命令。

## 检索原则

保持 Context Stuffing 架构：

文档编译 → 本体/关系组织 → 分层检索 → 引用回答

Embedding 是增强能力，不得成为基础检索唯一依赖。

## 会话规则

不同模型、不同任务、不同分支必须使用新会话。

推荐分工：
- Kimi K3：实施、测试、Bug修复
- GLM-5.2：架构分析、风险审查、PR Review

不要让同一个模型完成规划、实施、验收闭环。

## Git规则

未经用户明确授权禁止：
- git commit
- git push
- git merge
- git rebase
- git reset
- gh pr create

允许只读分析：
- git status
- git diff
- git log
- git show

## 验证要求

任何完成结论必须由当前会话运行命令支持。

禁止输出：
“应该通过”
“看起来没问题”
“历史测试已通过”

只能输出实际验证结果。