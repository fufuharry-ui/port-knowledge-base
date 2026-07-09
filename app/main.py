"""
app/main.py - FastAPI application entry point
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import ingest as ingest_router
from app.routers import search as search_router
from app.routers import knowledge as knowledge_router
from app.routers import qa as qa_router

app = FastAPI(
    title="Knowledge Base API",
    description="Intelligent knowledge base service based on Karpathy Context Stuffing",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_PREFIX = "/api/v1"

app.include_router(ingest_router.router, prefix=API_PREFIX, tags=["Ingest"])
app.include_router(search_router.router, prefix=API_PREFIX, tags=["Search"])
app.include_router(knowledge_router.router, prefix=API_PREFIX, tags=["Knowledge"])
app.include_router(qa_router.router, prefix=API_PREFIX, tags=["Q&A"])


@app.get("/health", tags=["System"])
async def health_check():
    return {"status": "ok", "version": "2.0.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)