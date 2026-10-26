from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from yomikae.config import Settings
from yomikae.pipeline import Engine


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("images", nargs="+", type=Path)
    ap.add_argument("--context-key")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    t = time.perf_counter()
    settings = replace(Settings.from_env(), cache_dir=Path(tempfile.mkdtemp(prefix="yomikae-")))
    engine = Engine(settings)
    print(f"load {time.perf_counter() - t:.1f}s")
    t = time.perf_counter()
    engine.warmup()
    print(f"warmup {time.perf_counter() - t:.1f}s")

    for i, path in enumerate(args.images):
        result = await engine.process(path.read_bytes(), args.context_key, i)
        out = args.out_dir / f"{path.stem}.rendered.webp"
        out.write_bytes(engine.cache.rendered_path(result["page_id"]).read_bytes())
        print(path.name, json.dumps(result["timings_ms"]))
        for b in result["bubbles"]:
            print("  ", b["box"], "V" if b["vertical"] else "H", b["source"], "->", b["translation"])
        print("  ->", out)


if __name__ == "__main__":
    asyncio.run(main())
