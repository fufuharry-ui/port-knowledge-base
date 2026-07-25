"""UAT Big-Loop: 隔离真实栈运行根(方案 C)。

把当前 api/scripts + raw/wiki/meta/originals 复制到 .uat/runtime/,从副本启动
真实 FastAPI(api.main:app)。核心引擎按 Path(__file__).parent.parent 语义工作,
所有写入落在副本;源数据指纹在启动前落盘,UAT 后由 verify_source_unchanged.py
逐文件 SHA-256 比对,确保真实知识库零污染。

设计要点(见 docs/superpowers/specs/2026-07-19-personal-kb-uat-big-loop-design.md §3/§ADR-001):
- 不复制 .env 到运行根(密钥只进子进程环境,不写证据);用 os.environ.setdefault
  把源 .env 的值加载到本进程,真实 env 优先。
- 每次启动删除并重建 runtime,manifest 记录源数据哈希,避免过期副本伪造证据。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIRS = ("raw", "wiki", "meta", "originals")
CODE_DIRS = ("api", "scripts")


def load_project_env(root: Path) -> None:
    """把源 .env 的键值加载进本进程环境(不复制 .env 文件到 runtime)。

    os.environ.setdefault:已存在的真实 env 不被覆盖(与 scripts/*.py 约定一致)。
    """
    env_file = root / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def fingerprint_data(root: Path) -> dict[str, str]:
    """对 DATA_DIRS 下所有文件逐个 SHA-256,返回 {相对路径: hash}。"""
    result: dict[str, str] = {}
    for directory in DATA_DIRS:
        base = root / directory
        if not base.exists():
            continue
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            relative = path.relative_to(root).as_posix()
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _rmtree_robust(path: Path, attempts: int = 6, delay: float = 1.0) -> None:
    """Windows:刚结束的进程可能未及时释放目录内文件句柄(WinError 32),
    导致 shutil.rmtree 失败。重试几次等待 OS 释放。
    """
    last_exc: Exception | None = None
    for _ in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except PermissionError as exc:
            last_exc = exc
            time.sleep(delay)
    if last_exc:
        raise last_exc


def prepare_runtime(runtime: Path, manifest: Path) -> None:
    """删除并重建 runtime 副本,写入源数据指纹 manifest。"""
    if runtime.exists():
        _rmtree_robust(runtime)
    runtime.mkdir(parents=True)

    for directory in CODE_DIRS + DATA_DIRS:
        source = PROJECT_ROOT / directory
        target = runtime / directory
        if source.exists():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            target.mkdir(parents=True)

    payload = {
        "project_root": str(PROJECT_ROOT),
        "runtime_root": str(runtime.resolve()),
        "source_data_sha256": fingerprint_data(PROJECT_ROOT),
    }
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, default=PROJECT_ROOT / ".uat" / "runtime")
    parser.add_argument("--manifest", type=Path, default=PROJECT_ROOT / ".uat" / "source-fingerprint.json")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()

    runtime = args.runtime.resolve()
    manifest = args.manifest.resolve()
    prepare_runtime(runtime, manifest)
    load_project_env(PROJECT_ROOT)

    # Windows:本进程 stdout 默认 GBK,/docs/{id}/recompile 的进程内 compile_doc 会
    # print(✅/📌) → UnicodeEncodeError 被 compile_doc except 吞掉并把状态置 error。
    # 重配 stdout/stderr 为 UTF-8,让进程内编译输出正常(子进程另有 PYTHONUTF8=1)。
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        os.environ.setdefault("PYTHONUTF8", "1")

    # 操作者批准的 UAT 临时覆盖(Option B,2026-07-24):.env 的 qwen3.6-plus 是推理模型,
    # 对 SUMMARY/ONTOLOGY 大结构化输出慢(实测 ~300s/compile,远超 60s NFR;QA 25s 同理)。
    # UAT 用 qwen-plus(非推理,实测结构化调用 ~1.5s)以满足 NFR。**只进本进程环境,不改 .env。**
    # 生产/常规运行仍用 .env 的模型。
    for _model_var in ("COMPILE_MODEL", "ONTOLOGY_MODEL", "RELATE_MODEL", "SEARCH_MODEL"):
        os.environ[_model_var] = "qwen-plus"

    os.chdir(runtime)
    sys.path.insert(0, str(runtime))
    # Windows keep-alive 竞态修复(2026-07-25):uvicorn 默认 5s 关闭空闲 keep-alive
    # 连接,而 expect.poll 的轮询间隔也是 5s —— 客户端恰好在服务端关闭后复用同一
    # 连接 → read ECONNRESET(且 apiRequestContext 不复建,后续全部 poll 连续失败)。
    # 提高 keep-alive 到 75s(>> 任何轮询间隔),消除竞态。仅测试 harness,非产品代码。
    uvicorn.run(
        "api.main:app",
        host="127.0.0.1",
        port=args.port,
        log_level="info",
        timeout_keep_alive=75,
    )


if __name__ == "__main__":
    main()
