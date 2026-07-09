"""
app/routers/ingest.py - Document ingest routes
POST /api/v1/upload, GET /api/v1/docs, GET /api/v1/docs/{doc_id}
"""

import shutil
import sys
from pathlib import Path
from typing import Optional

import yaml
from urllib.parse import unquote
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File

from app.config import Settings, get_settings
from app.schemas import DocListResponse, DocMeta, UploadResponse, WikiIndexResponse
from app.utils.background import compile_then_relate

_root = str(Path(__file__).parent.parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from scripts.ingest import ingest_file

router = APIRouter()

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".md", ".markdown", ".html", ".htm", ".txt"}


@router.post("/upload", response_model=UploadResponse)
def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
):
    raw_filename = file.filename or "upload"
    try:
        raw_filename = raw_filename.encode("latin-1").decode("utf-8")
    except Exception:
        pass
        
    import re
    safe_filename = re.sub(r'[\\/:*?"<>|]', '_', raw_filename)
    
    suffix = Path(safe_filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported format '{suffix}', supported: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    settings.originals_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.originals_dir / safe_filename
    with open(dest, "wb") as f_out:
        shutil.copyfileobj(file.file, f_out)

    import scripts.ingest as ingest_mod
    ingest_mod.BASE_DIR = settings.base_dir
    ingest_mod.RAW_DIR = settings.raw_dir
    ingest_mod.ORIGINALS_DIR = settings.originals_dir
    ingest_mod.WIKI_DIR = settings.wiki_dir
    ingest_mod.INDEX_FILE = settings.index_file

    result = ingest_file(dest)

    if result is None:
        return UploadResponse(skipped=True, message="File already exists (SHA256 duplicate), skipped.")

    doc_id: str = result.get("id") or result.get("doc_id", "")

    background_tasks.add_task(
        compile_then_relate,
        doc_id=doc_id,
        base_dir=settings.base_dir,
        settings=settings,
    )

    return UploadResponse(
        doc_id=doc_id,
        title=result.get("title", safe_filename),
        status=result.get("status", "raw"),
        char_count=result.get("char_count"),
    )


@router.get("/docs", response_model=DocListResponse)
async def list_docs(settings: Settings = Depends(get_settings)):
    if not settings.index_file.exists():
        return DocListResponse(documents=[], total=0)

    with open(settings.index_file, "r", encoding="utf-8") as f:
        index = yaml.safe_load(f) or {"documents": []}

    docs = [
        DocMeta(
            id=d.get("id", ""),
            title=d.get("title"),
            status=d.get("status"),
            char_count=d.get("char_count"),
            language=d.get("language"),
            ingested_at=d.get("ingested_at"),
            source_type=d.get("source_type"),
        )
        for d in index.get("documents", [])
    ]
    return DocListResponse(documents=docs, total=len(docs))


@router.get("/docs/{doc_id}", response_model=DocMeta)
async def get_doc(doc_id: str, settings: Settings = Depends(get_settings)):
    meta_path = settings.raw_dir / f"{doc_id}.meta.yaml"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = yaml.safe_load(f) or {}

    return DocMeta(
        id=meta.get("id", doc_id),
        title=meta.get("title"),
        status=meta.get("status"),
        char_count=meta.get("char_count"),
        language=meta.get("language"),
        ingested_at=meta.get("ingested_at"),
        source_type=meta.get("source_type"),
    )