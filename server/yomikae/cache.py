from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from .model import ContextLine

CONTEXT_PAGES = 2
CONTEXT_REACH = 5
RECALL_MIN_CHARS = 8
RECALL_CHARS_PER_EDIT = 8
RECALL_MIN_WORD_CHARS = 2
NAME_RATIO = 0.8
NOISE = re.compile(r"[\s.,、。・ー一っッ~～\-]+")
VERSION_DIR = re.compile(r"v(\d+)")
LEGACY_DIR = re.compile(r"[0-9a-f]{64}")
NAME = re.compile(r"\b[A-Z][a-z]+-(?:san|kun|chan|sama|senpai|sensei|dono|tan|nee|nii|neesan|niisan|neechan|niichan)\b")


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


class PageCache:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.writing: set[str] = set()
        self.size = sum(dir_size(d) for d in self.page_dirs())

    def rendered_path(self, page_id: str) -> Path:
        return next((self.root / page_id).glob("rendered.*"), self.root / page_id / "rendered.webp")

    def load(self, page_id: str) -> dict | None:
        path = self.root / page_id / "result.json"
        try:
            if not self.rendered_path(page_id).exists():
                return None
            result = json.loads(path.read_text())
            os.utime(path)
        except (OSError, ValueError):
            return None
        return result

    def store(self, page_id: str, rendered_name: str, rendered: bytes, result: dict) -> None:
        data = json.dumps(result, ensure_ascii=False).encode()
        with self.lock:
            self.writing.add(page_id)
        try:
            write_atomic(self.root / page_id / rendered_name, rendered)
            write_atomic(self.root / page_id / "result.json", data)
            with self.lock:
                self.size += len(rendered) + len(data)
        finally:
            with self.lock:
                self.writing.discard(page_id)

    def all_results(self):
        for path in self.root.glob("*/result.json"):
            try:
                yield json.loads(path.read_text())
            except (OSError, ValueError):
                continue

    def page_dirs(self) -> list[Path]:
        return [d for d in self.root.iterdir() if d.is_dir() and not d.is_symlink() and not d.name.startswith(".")]

    def over(self, max_bytes: int) -> bool:
        return self.size > max_bytes

    def evict(self, max_bytes: int) -> list[dict]:
        pages = sorted((last_read(d), d, dir_size(d)) for d in self.page_dirs())
        with self.lock:
            self.size = sum(size for _, _, size in pages)
        evicted = []
        for read, d, size in pages[:-1]:
            if self.size <= max_bytes:
                break
            if last_read(d) != read:
                continue
            result = self.remove(d)
            if result is not None:
                evicted.append(result)
                with self.lock:
                    self.size -= size
        return evicted

    def clear(self) -> list[dict]:
        removed = [r for d in self.page_dirs() if (r := self.remove(d)) is not None]
        with self.lock:
            self.size = sum(dir_size(d) for d in self.page_dirs())
        return removed

    def remove(self, page_dir: Path) -> dict | None:
        with self.lock:
            if page_dir.name in self.writing:
                return None
            path = page_dir / "result.json"
            try:
                result = json.loads(path.read_text())
            except (OSError, ValueError):
                result = {}
            path.unlink(missing_ok=True)
            shutil.rmtree(page_dir, ignore_errors=True)
        return result


def prune_stale(root: Path) -> int:
    current = VERSION_DIR.fullmatch(root.name)
    if current is None or not root.parent.is_dir():
        return 0
    stale = [
        d
        for d in root.parent.iterdir()
        if d.is_dir()
        and not d.is_symlink()
        and (LEGACY_DIR.fullmatch(d.name) or ((m := VERSION_DIR.fullmatch(d.name)) and int(m.group(1)) < int(current.group(1))))
    ]
    for d in stale:
        shutil.rmtree(d, ignore_errors=True)
    return len(stale)


def last_read(page_dir: Path) -> float:
    try:
        return (page_dir / "result.json").stat().st_mtime
    except OSError:
        return 0.0


def dir_size(page_dir: Path) -> int:
    total = 0
    try:
        for f in page_dir.iterdir():
            total += f.stat().st_size
    except OSError:
        pass
    return total


class ContextStore:

    def __init__(self):
        self.pages: dict[str, dict[float, list[ContextLine]]] = {}

    def rebuild(self, cache: PageCache) -> None:
        for r in cache.all_results():
            if r.get("context_key"):
                self.add(r["context_key"], r.get("page_index"), [ContextLine(b["source"], b["translation"]) for b in r["bubbles"]])

    def position(self, key: str, page_index: int | None) -> float:
        if page_index is not None:
            return float(page_index)
        known = self.pages.get(key, {})
        return max(known, default=-1.0) + 0.5

    def before(self, key: str | None, page_index: int | None) -> list[list[ContextLine]]:
        if not key or key not in self.pages:
            return []
        pos = self.position(key, page_index)
        earlier = sorted(p for p in self.pages[key] if pos - CONTEXT_REACH <= p < pos)[-CONTEXT_PAGES:]
        return [self.pages[key][p] for p in earlier if self.pages[key][p]]

    def add(self, key: str, page_index: int | None, lines: list[ContextLine]) -> None:
        pos = self.position(key, page_index)
        self.pages.setdefault(key, {})[pos] = [line for line in lines if line.translation]

    def forget(self, result: dict) -> None:
        key = result.get("context_key")
        pages = self.pages.get(key) if key else None
        if not pages:
            return
        lines = [ContextLine(b["source"], b["translation"]) for b in result.get("bubbles", []) if b.get("translation")]
        index = result.get("page_index")
        preferred = [float(index)] if index is not None else []
        for pos in preferred + sorted(pages):
            if pages.get(pos) == lines:
                del pages[pos]
                break
        if not pages:
            del self.pages[key]

    def memory(self, key: str | None) -> list[ContextLine]:
        pages = self.pages.get(key, {}) if key else {}
        return [line for p in sorted(pages) for line in pages[p]]

    def recall(self, key: str | None, sources: list[str]) -> list[str | None]:
        known: dict[str, str] = {}
        for line in self.memory(key):
            known.setdefault(normalize(line.source), line.translation)
        return [lookup(known, normalize(s)) for s in sources]

    def names(self, key: str | None) -> dict[str, str]:
        seen: dict[str, dict[str, str]] = {}
        kept: dict[str, str] = {}
        pages = self.pages.get(key, {}) if key else {}
        for p in sorted(pages):
            for line in pages[p]:
                for m in NAME.finditer(line.translation):
                    if m.group(0) not in kept:
                        seen.setdefault(m.group(0), {}).setdefault(normalize(line.source), line.source)
            kept = {}
            for name, lines in sorted(seen.items(), key=lambda item: -len(item[1])):
                if not any(same_name(name, k) for k in kept):
                    kept[name] = next(iter(lines.values()))
        return kept


def normalize(source: str) -> str:
    return NOISE.sub("", unicodedata.normalize("NFKC", source))


def lookup(known: dict[str, str], key: str) -> str | None:
    if sum(ch.isalnum() for ch in key) < RECALL_MIN_WORD_CHARS:
        return None
    if key in known:
        return known[key]
    if len(key) < RECALL_MIN_CHARS:
        return None
    edits, translation = min(((edit_distance(key, k), t) for k, t in known.items()), key=lambda pair: pair[0], default=(len(key), None))
    return translation if edits <= len(key) // RECALL_CHARS_PER_EDIT else None


def edit_distance(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > len(a) // RECALL_CHARS_PER_EDIT:
        return abs(len(a) - len(b))
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def same_name(a: str, b: str) -> bool:
    (base_a, honorific_a), (base_b, honorific_b) = a.rsplit("-", 1), b.rsplit("-", 1)
    return honorific_a == honorific_b and SequenceMatcher(None, base_a, base_b).ratio() >= NAME_RATIO
