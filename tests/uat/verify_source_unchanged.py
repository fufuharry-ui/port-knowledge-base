"""UAT Big-Loop: 比对源数据指纹,证明 UAT 未污染真实知识库。

读取 live_server.prepare_runtime 写入的 manifest 中的 source_data_sha256,
重新对源数据逐文件 SHA-256,逐键比对。任何 missing/added/changed 都判定为污染。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from live_server import PROJECT_ROOT, fingerprint_data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=PROJECT_ROOT / ".uat" / "source-fingerprint.json",
    )
    args = parser.parse_args()

    expected = json.loads(args.manifest.read_text(encoding="utf-8"))["source_data_sha256"]
    actual = fingerprint_data(PROJECT_ROOT)
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        added = sorted(set(actual) - set(expected))
        changed = sorted(k for k in set(expected) & set(actual) if expected[k] != actual[k])
        raise SystemExit(
            json.dumps(
                {"status": "changed", "missing": missing, "added": added, "changed": changed},
                ensure_ascii=False,
                indent=2,
            )
        )
    print(json.dumps({"status": "unchanged", "files": len(actual)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
