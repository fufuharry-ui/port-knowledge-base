"""E005 运行时门禁原语:编译运行时配置、单 API 实例锁、服务就绪门禁。

对应设计文档第 6 节(部署模型与配置)与第 19 节(服务门禁)。
本模块不读取真实知识库目录;所有路径由调用方以 base_dir 显式注入。
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import portalocker

ENV_TRANSACTION_DIR = "COMPILE_TRANSACTION_DIR"
ENV_TIMEOUT_SECONDS = "COMPILE_TIMEOUT_SECONDS"
ENV_TERMINATION_GRACE_SECONDS = "COMPILE_TERMINATION_GRACE_SECONDS"

DEFAULT_TIMEOUT_SECONDS = 1800
MIN_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 86400

DEFAULT_TERMINATION_GRACE_SECONDS = 5
MIN_TERMINATION_GRACE_SECONDS = 1
MAX_TERMINATION_GRACE_SECONDS = 60


@dataclass(frozen=True)
class CompileRuntimeConfig:
    transaction_dir: Path
    upload_intake_dir: Path
    instance_lock_path: Path
    timeout_seconds: int
    termination_grace_seconds: int


def _parse_bounded_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None
    if not minimum <= value <= maximum:
        raise ValueError(
            f"{name} must be between {minimum} and {maximum}, got {value}"
        )
    return value


def load_compile_runtime_config(
    base_dir: Path, env: Mapping[str, str] | None = None
) -> CompileRuntimeConfig:
    """解析编译运行时配置。

    - COMPILE_TRANSACTION_DIR: 相对路径以 base_dir 解析,绝对路径原样使用;
    - COMPILE_TIMEOUT_SECONDS: 60–86400,默认 1800;
    - COMPILE_TERMINATION_GRACE_SECONDS: 1–60,默认 5;
    - 非法值抛出命名对应环境变量的 ValueError。
    """
    base_dir = Path(base_dir)
    if env is None:
        env = os.environ

    runtime_root = base_dir / ".runtime"

    raw_transaction_dir = env.get(ENV_TRANSACTION_DIR)
    if raw_transaction_dir and str(raw_transaction_dir).strip():
        candidate = Path(str(raw_transaction_dir).strip())
        transaction_dir = (
            candidate if candidate.is_absolute() else base_dir / candidate
        )
    else:
        transaction_dir = runtime_root / "compile-transactions"

    return CompileRuntimeConfig(
        transaction_dir=transaction_dir,
        upload_intake_dir=runtime_root / "upload-intake",
        instance_lock_path=runtime_root / "api-instance.lock",
        timeout_seconds=_parse_bounded_int(
            env,
            ENV_TIMEOUT_SECONDS,
            DEFAULT_TIMEOUT_SECONDS,
            MIN_TIMEOUT_SECONDS,
            MAX_TIMEOUT_SECONDS,
        ),
        termination_grace_seconds=_parse_bounded_int(
            env,
            ENV_TERMINATION_GRACE_SECONDS,
            DEFAULT_TERMINATION_GRACE_SECONDS,
            MIN_TERMINATION_GRACE_SECONDS,
            MAX_TERMINATION_GRACE_SECONDS,
        ),
    )


class ApiInstanceLock:
    """跨平台单 API 实例排他锁。

    acquire() 通过 portalocker.Lock(path, mode="a", timeout=0) 非阻塞获取;
    第二个句柄获取同一路径时立即失败,以此保证单 API 实例合同。
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._handle: portalocker.Lock | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            raise RuntimeError("ApiInstanceLock already acquired")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = portalocker.Lock(str(self._path), mode="a", timeout=0)
        handle.acquire()
        self._handle = handle

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            handle.release()

    def __enter__(self) -> "ApiInstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()


class ServiceReadiness:
    def __init__(self) -> None:
        self._mode = "ready"
        self._reason_code: str | None = None
        self._lock = threading.Lock()

    def mark_recovery_required(self, reason_code: str) -> None:
        with self._lock:
            self._mode = "recovery_required"
            self._reason_code = reason_code

    def require_ready(self) -> None:
        with self._lock:
            if self._mode != "ready":
                raise RuntimeError("recovery_required")

    def snapshot(self) -> tuple[str, str | None]:
        with self._lock:
            return self._mode, self._reason_code
