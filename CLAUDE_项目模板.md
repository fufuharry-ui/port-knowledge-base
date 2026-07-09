# CLAUDE.md — 项目模板

> 本文件提供本项目的通用 AI 开发指引。
> 详细项目信息请查看根目录 `CLAUDE.md`。

## 项目类型
- Python FastAPI 后端
- Next.js 前端
- 知识库系统 (Context Stuffing 架构)

## 关键约定
1. 不要重写 scripts/ 下的核心引擎
2. 不要迁移到外部向量数据库
3. 所有产品化工作封装现有引擎
