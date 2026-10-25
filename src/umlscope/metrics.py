"""Load crapper's .metrics/crap.edn and join it onto scanned modules.

crapper (github.com/unclebob/crapper) writes:

    {:entries [{:name "place" :namespace "demo.Board"
                :complexity 3 :coverage 75.0 :crap 3.1}]}

For Python, :namespace is the dotted module path, or ``module.Class`` for
methods; :name is the function or method name. The dotted path may carry
leading segments the scanner does not use (for example ``src.``), so the join
tries progressively shorter suffixes and reports whatever it could not place.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import edn


@dataclass
class Score:
    cc: float | None
    cov: float | None       # percent 0..100, None when not measured
    crap: float | None


@dataclass
class MetricsSnapshot:
    path: Path | None = None
    entries: int = 0
    matched: int = 0
    unmatched: list[str] = field(default_factory=list)
    # (module_id, class_qualname or "", function name) -> Score
    scores: dict[tuple[str, str, str], Score] = field(default_factory=dict)
    error: str | None = None

    def to_json(self):
        return {
            "path": str(self.path) if self.path else None,
            "entries": self.entries,
            "matched": self.matched,
            "unmatched": self.unmatched[:25],
            "unmatched_count": len(self.unmatched),
            "error": self.error,
        }


def _read_entries(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text) if path.suffix == ".json" else edn.loads(text)
    if isinstance(data, dict):
        data = data.get("entries", [])
    return [e for e in data if isinstance(e, dict)]


def _place(namespace: str, module_ids: set[str], class_names: dict[str, set[str]]):
    parts = namespace.replace("/", ".").replace("::", ".").split(".")
    for drop in range(len(parts)):
        tail = parts[drop:]
        for k in range(len(tail), 0, -1):
            mod = ".".join(tail[:k])
            if mod in module_ids:
                cls = ".".join(tail[k:])
                if not cls or cls in class_names.get(mod, ()):
                    return mod, cls
    return None


def load(metrics_file: Path | None, modules) -> MetricsSnapshot:
    snap = MetricsSnapshot(path=metrics_file)
    if metrics_file is None or not metrics_file.exists():
        return snap
    try:
        entries = _read_entries(metrics_file)
    except Exception as exc:  # malformed snapshot should not kill the viewer
        snap.error = f"{type(exc).__name__}: {exc}"
        return snap

    module_ids = set(modules)
    class_names = {m.id: {c.name for c in m.classes} for m in modules.values()}
    raw = [_raw_cov(e.get("coverage")) for e in entries]
    as_ratio = all(c is None or c <= 1.0 for c in raw) and any(c not in (None, 0, 0.0) for c in raw)

    snap.entries = len(entries)
    for entry, cov in zip(entries, raw):
        ns, name = str(entry.get("namespace", "")), str(entry.get("name", ""))
        placed = _place(ns, module_ids, class_names)
        if not placed or not name:
            snap.unmatched.append(f"{ns}/{name}")
            continue
        if cov is not None and as_ratio:
            cov *= 100.0
        score = Score(_num(entry.get("complexity")), cov, _num(entry.get("crap")))
        key = (placed[0], placed[1], name)
        prev = snap.scores.get(key)
        # property getter/setter pairs share a name: keep the worse one
        if prev is None or (score.crap or 0) > (prev.crap or 0):
            snap.scores[key] = score
        snap.matched += 1
    return snap


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _raw_cov(v):
    return _num(v)
