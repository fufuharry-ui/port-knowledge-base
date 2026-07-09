"""
app/main.py — FastAPI 应用主入口
初始化 FastAPI app，注册CORS、所有路由，提供健康检查端点。
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import ingest as ingest_router
from app.routers import search as search_router
from app.routers import knowledge as knowledge_router
from app.routers import qa as qa_router

app = FastAPI(
    title="知识库 API",
    description="基于 Karpathy Context Stuffing 哲学的智能知识库服务",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ─── CORS（允许前端 Next.js 跨域访问）────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # 生产环境替换为具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── 路由注册 ─────────────────────────────────────────────────────────────────
API_PREFIX = "/api/v1"

app.include_router(ingest_router.router, prefix=API_PREFIX, tags=["Ingest"])
app.include_router(search_router.router, prefix=API_PREFIX, tags=["Search"])
app.include_router(knowledge_router.router, prefix=API_PREFIX, tags=["Knowledge"])
app.include_router(qa_router.router, prefix=API_PREFIX, tags=["Q&A"])


# ─── 健康检查 ─────────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
async def health_check():
    return {"status": "ok", "version": "2.0.0"}


# ─── 启动入口 ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
