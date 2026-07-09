import os
import requests
from pathlib import Path


def _load_dot_env():
    """Load .env from project root (same logic as app/config.py)."""
    env_file = Path(__file__).parent.parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


# Load .env before reading any env vars
_load_dot_env()


class EmbeddingClient:
    def __init__(self):
        self.api_key = os.environ.get("EMBEDDING_API_KEY", "")
        self.base_url = os.environ.get("EMBEDDING_BASE_URL", "https://api.openai.com/v1")
        self.model = os.environ.get("EMBEDDING_MODEL_NAME", "text-embedding-3-small")

        if not self.api_key:
            raise RuntimeError(
                "[EmbeddingClient] EMBEDDING_API_KEY 未设置。\n"
                "请在 .env 或环境变量中配置：\n"
                "  EMBEDDING_API_KEY=<your-key>\n"
                "  EMBEDDING_BASE_URL=<api-base-url>\n"
                "  EMBEDDING_MODEL_NAME=<model-name>"
            )

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        })

    def get_embedding(self, text: str) -> list[float]:
        if not text.strip():
            return [0.0] * 1536

        try:
            res = self.session.post(
                f"{self.base_url.rstrip('/')}/embeddings",
                json={"input": text, "model": self.model},
                timeout=10
            )
            res.raise_for_status()
            data = res.json()
            return data["data"][0]["embedding"]
        except Exception as e:
            print(f"[ERROR] Embedding fetch failed: {e}")
            return [0.0] * 1536
