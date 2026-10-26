from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

LEADING_WORDS = frozenset(
    "a an the of to in on at for with from by into onto about over under than as and or but nor so that if because "
    "my your his her its our their this these those very".split()
)
SOFT_WORDS = frozenset("i we you he she they it is are was were be been will would can could do does did don't i'm".split())
OPENING_WORDS = frozenset("and or but because so to with for from in on at of than as if when while".split())

SENTENCE_END = 0.0
CLAUSE_END = 1.0
BEFORE_OPENER = 2.0
MID_PHRASE = 4.0
AFTER_SOFT = 10.0
AFTER_LEADING = 40.0
HYPHEN_BREAK = 40.0
AFTER_HYPHEN = 10.0
LONE_SHORT_WORD = 15.0
SHAPE = 25.0
LOZENGE = 0.3
SHORT_WORD = 3
MIN_SPLIT = 6
MIN_HEAD = 2
MIN_TAIL = 3
PUNCT_ONLY = re.compile(r"^[\W_]+$")
SYMBOLS = set("♡♥♪♫★☆")


@dataclass(frozen=True)
class Atom:

    text: str
    joined: bool = False


@dataclass(frozen=True)
class Lined:

    lines: list[str]
    cost: float
    faults: int


def atoms(text: str) -> list[Atom]:
    out: list[Atom] = []
    for token in text.split():
        if out and PUNCT_ONLY.match(token) and not set(token) & SYMBOLS:
            out[-1] = Atom(f"{out[-1].text} {token}", out[-1].joined)
        else:
            out.extend(Atom(part, joined=m > 0) for m, part in enumerate(re.findall(r"[^-]+-+(?=[^\W_])|.+", token)))
    return out


def bare(word: str) -> str:
    return re.sub(r"^[\W_]+|[\W_]+$", "", word).lower().replace("’", "'")


def break_cost(before: Atom, after: Atom) -> float:
    if after.joined:
        return AFTER_HYPHEN if before.text.endswith("-") else HYPHEN_BREAK
    end = before.text.rstrip("\"'”’)」")
    if end.endswith(("!", "?", ".")) and not end.endswith(("...", "…")):
        return SENTENCE_END
    if end.endswith((",", ";", ":", "…", "...", "~", "-", "—", "–")) or before.text[-1] in SYMBOLS:
        return CLAUSE_END
    word = bare(before.text.rsplit("-", 1)[-1])
    if word in LEADING_WORDS:
        return AFTER_LEADING
    if word in SOFT_WORDS:
        return AFTER_SOFT
    if bare(after.text) in OPENING_WORDS:
        return BEFORE_OPENER
    return MID_PHRASE


def lone_short(line: list[Atom]) -> bool:
    if len(line) != 1 or line[0].text in SYMBOLS:
        return False
    letters = sum(ch.isalnum() for ch in line[0].text)
    return letters <= SHORT_WORD


def targets(widths: list[int], total: float) -> list[float]:
    n = len(widths)
    mid = (n - 1) / 2
    profile = [w * (1 - LOZENGE * abs(i - mid) / mid) if n > 2 else w for i, w in enumerate(widths)]
    scale = total / sum(profile)
    return [p * scale for p in profile]


def break_lines(items: list[Atom], widths: list[int], measure: Callable[[str], float]) -> Lined | None:
    n, k = len(widths), len(items)
    if k < n:
        return None
    space, hyphen = measure(" "), measure("-")
    run = [0.0]
    for a in items:
        run.append(run[-1] + measure(a.text) + (0 if a.joined else space))

    def line_width(i: int, j: int) -> float:
        w = run[j] - run[i] - (0 if items[i].joined else space)
        return w + hyphen if j < k and items[j].joined and not items[j - 1].text.endswith("-") else w

    def cut(j: int) -> bool:
        return 0 < j < k and items[j].joined and not items[j - 1].text.endswith("-")

    def twice(i: int, j: int) -> bool:
        return cut(i) and cut(j) and all(items[m].joined for m in range(i + 1, j))

    breaks = [break_cost(a, b) for a, b in zip(items, items[1:])]
    short = [lone_short([a]) for a in items] if n > 1 else [False] * k
    total = line_width(0, k) - space * (n - 1)
    goal = targets(widths, total)
    widest = max(goal)
    inf = float("inf")
    best = [[inf] * (k + 1) for _ in range(n + 1)]
    back = [[0] * (k + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    for line in range(n):
        for j in range(line + 1, k - (n - line - 1) + 1):
            for i in range(j - 1, line - 1, -1):
                w = line_width(i, j)
                if w > widths[line]:
                    break
                if best[line][i] == inf or twice(i, j):
                    continue
                cost = best[line][i] + SHAPE * ((w - goal[line]) / widest) ** 2
                if j < k:
                    cost += breaks[j - 1]
                if j == i + 1 and short[i]:
                    cost += LONE_SHORT_WORD
                if cost < best[line + 1][j]:
                    best[line + 1][j], back[line + 1][j] = cost, i
    if best[n][k] == inf:
        return None
    cuts = [k]
    for line in range(n, 0, -1):
        cuts.append(back[line][cuts[-1]])
    cuts.reverse()
    lines = []
    faults = 0
    for i, j in zip(cuts, cuts[1:]):
        text = "".join(a.text if a.joined or m == i else f" {a.text}" for m, a in enumerate(items[i:j], i))
        lines.append(text + "-" if j < k and items[j].joined and not items[j - 1].text.endswith("-") else text)
        faults += j < k and breaks[j - 1] >= AFTER_LEADING
    return Lined(lines, best[n][k], faults)


def names(source: str) -> frozenset[str]:
    out = set()
    after_end = True
    for word in source.split():
        core = re.sub(r"[^\w'-]", "", word)
        if core[:1].isupper() and not after_end:
            out.add(core.upper())
        after_end = word.endswith((".", "!", "?", "…"))
    return frozenset(out)


@lru_cache(maxsize=1)
def dictionary():
    import pyphen

    return pyphen.Pyphen(lang="en_US")


def syllables(word: str, keep: frozenset[str]) -> list[str]:
    head = re.match(r"[^\W\d_]+", word)
    if not head or head.end() < MIN_SPLIT or "'" in word or "’" in word or "-" in word or head.group().upper() in keep:
        return [word]
    letters = head.group()
    cuts = [c for c in dictionary().positions(letters.lower()) if c >= MIN_HEAD and len(letters) - c >= MIN_TAIL]
    return [word[i:j] for i, j in zip([0, *cuts], [*cuts, len(word)])]


def hyphenated(items: list[Atom], widest: int, measure: Callable[[str], float], keep: frozenset[str]) -> list[Atom]:
    out: list[Atom] = []
    for a in items:
        if measure(a.text) <= widest:
            out.append(a)
            continue
        for m, part in enumerate(syllables(a.text, keep)):
            out.append(Atom(part, joined=m > 0 or a.joined))
    return out
