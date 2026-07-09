# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A **Karpathy-style "Context Stuffing" knowledge base** for Chinese-language **port smart-port (港口智慧化)** documents.

## Guardrails

1. **Do not rewrite the four core engines** in `scripts/`.
2. **Do not migrate to an external distributed vector DB.**

## Commands

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
python scripts/ingest.py
python scripts/compile.py
python scripts/search.py "岸桥 远控 网络 延迟"
python scripts/relate.py doc_20260405_001
uvicorn api.main:app --reload --port 8000
cd frontend && npm run dev
```
