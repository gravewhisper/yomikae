from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from .model import CastEntry, ContextLine

CONTEXT_PAGES = 2
CONTEXT_REACH = 5
RECALL_MIN_CHARS = 8
RECALL_CHARS_PER_EDIT = 8
RECALL_MIN_WORD_CHARS = 2
NAME_RATIO = 0.8
NOISE = re.compile(r"[\s.,、。・ー一っッ~～\-]+")
VERSION_DIR = re.compile(r"v(\d+)")
LEGACY_DIR = re.compile(r"[0-9a-f]{64}")
SAVED_SOURCES = ("furigana", "user")
KINDS = ("name", "term")
HONORIFIC_KANA = {
    "san": ("さん",), "kun": ("くん", "君"), "chan": ("ちゃん",), "sama": ("さま", "様"), "senpai": ("先輩", "せんぱい"),
    "sensei": ("先生", "せんせい"), "dono": ("殿", "どの"), "nee": ("姉", "ねえ", "ねぇ"), "nii": ("兄", "にい", "にぃ"),
}
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
            before = dir_size(self.root / page_id)
            write_atomic(self.root / page_id / rendered_name, rendered)
            write_atomic(self.root / page_id / "result.json", data)
            with self.lock:
                self.size += dir_size(self.root / page_id) - before
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


@dataclass
class ContextPage:
    position: float
    lines: list[ContextLine]
    fixed: list[ContextLine]
    filler: bool = False


class ContextStore:

    def __init__(self, names_dir: Path | None = None):
        self.pages: dict[str, dict[str, ContextPage]] = {}
        self.names_dir = names_dir
        self.saved: dict[str, list[CastEntry]] = {}

    def rebuild(self, cache: PageCache) -> None:
        for r in cache.all_results():
            if r.get("context_key"):
                lines = [ContextLine(b["source"], b["translation"]) for b in r["bubbles"]]
                self.add(r["context_key"], r["page_id"], r.get("page_index"), lines, [lines[i] for i in r.get("fixed", []) if i < len(lines)])

    def ordered(self, key: str | None) -> list[ContextPage]:
        return sorted(self.pages.get(key, {}).values(), key=lambda p: p.position) if key else []

    def position(self, key: str, page_index: int | None, page_id: str | None = None) -> float:
        if page_index is not None:
            return float(page_index)
        known = self.pages.get(key, {})
        if page_id in known:
            return known[page_id].position
        return max((p.position for p in known.values()), default=-1.0) + 0.5

    def before(self, key: str | None, page_index: int | None) -> list[list[ContextLine]]:
        if not key or key not in self.pages:
            return []
        pos = self.position(key, page_index)
        earlier = [p for p in self.ordered(key) if pos - CONTEXT_REACH <= p.position < pos and not p.filler][-CONTEXT_PAGES:]
        return [p.lines for p in earlier if p.lines]

    def add(self, key: str, page_id: str, page_index: int | None, lines: list[ContextLine], fixed: list[ContextLine] = (), filler: bool = False) -> None:
        pos = self.position(key, page_index, page_id)
        self.pages.setdefault(key, {})[page_id] = ContextPage(pos, [line for line in lines if line.translation], [line for line in fixed if line.translation], filler)

    def forget(self, result: dict) -> None:
        key = result.get("context_key")
        pages = self.pages.get(key) if key else None
        if not pages:
            return
        pages.pop(result.get("page_id"), None)
        if not pages:
            del self.pages[key]

    def memory(self, key: str | None) -> list[ContextLine]:
        return [line for p in self.ordered(key) for line in p.lines]

    def recall(self, key: str | None, sources: list[str]) -> list[str | None]:
        known: dict[str, str] = {}
        for line in [line for p in self.ordered(key) for line in p.fixed] + self.memory(key):
            known.setdefault(normalize(line.source), line.translation)
        return [lookup(known, normalize(s)) for s in sources]

    def names(self, key: str | None) -> dict[str, str]:
        seen: dict[str, dict[str, str]] = {}
        kept: dict[str, str] = {}
        for p in self.ordered(key):
            for line in p.lines:
                for m in NAME.finditer(line.translation):
                    name = m.group(0).rsplit("-", 1)[0]
                    if name not in kept:
                        seen.setdefault(name, {}).setdefault(normalize(line.source), line.source)
            kept = {}
            for name, lines in sorted(seen.items(), key=lambda item: -len(item[1])):
                if not any(same_name(name, k) for k in kept):
                    kept[name] = next(iter(lines.values()))
        return kept


    def names_path(self, key: str) -> Path | None:
        return self.names_dir / f"{hashlib.sha256(key.encode()).hexdigest()}.json" if self.names_dir else None

    def saved_names(self, key: str) -> list[CastEntry]:
        if key not in self.saved:
            path = self.names_path(key)
            try:
                rows = json.loads(path.read_text())["names"] if path else []
                self.saved[key] = [CastEntry(r["ja"], r["en"], r.get("reading", ""), r["source"], r.get("kind") if r.get("kind") in KINDS else "name") for r in rows if r.get("source") in SAVED_SOURCES]
            except (OSError, ValueError, KeyError, TypeError):
                self.saved[key] = []
        return self.saved[key]

    def save_names(self, key: str, entries: list[CastEntry]) -> None:
        self.saved[key] = entries
        path = self.names_path(key)
        if path:
            rows = [{"ja": e.ja, "en": e.en, "reading": e.reading, "source": e.source, "kind": e.kind} for e in entries]
            write_atomic(path, json.dumps({"context_key": key, "names": rows}, ensure_ascii=False, indent=1).encode())

    def cast(self, key: str | None) -> list[CastEntry]:
        if not key:
            return []
        saved = self.saved_names(key)
        users = [e for e in saved if e.source == "user"]
        kept = users + [e for e in saved if e.source != "user" and not any(same_entry(e, u) for u in users)]
        auto = [CastEntry(line, name) for name, line in self.names(key).items() if not any(covers(e, name, line) for e in kept)]
        return kept + auto

    def set_name(self, key: str, entry: CastEntry) -> None:
        self.save_names(key, [e for e in self.saved_names(key) if not same_entry(e, entry)] + [entry])

    def learn_names(self, key: str | None, entries: list[CastEntry]) -> None:
        if not key:
            return
        saved = self.saved_names(key)
        fresh = [e for e in entries if not any(same_entry(e, s) for s in saved)]
        if fresh:
            self.save_names(key, saved + fresh)


def same_entry(a: CastEntry, b: CastEntry) -> bool:
    return a.ja == b.ja if a.ja and b.ja else base_name(a.en) == base_name(b.en)


def covers(entry: CastEntry, name: str, line: str) -> bool:
    honorific = name.rsplit("-", 1)[-1].lower()
    if entry.ja and any(entry.ja + kana in line for kana in HONORIFIC_KANA.get(honorific, ())):
        return True
    return SequenceMatcher(None, base_name(entry.en).lower(), base_name(name).lower()).ratio() >= NAME_RATIO


def base_name(name: str) -> str:
    return name.rsplit("-", 1)[0] if NAME.fullmatch(name) else name


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
    return SequenceMatcher(None, a, b).ratio() >= NAME_RATIO
