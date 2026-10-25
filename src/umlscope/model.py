"""Assemble scan + metrics into the JSON document the browser renders."""

from __future__ import annotations

import hashlib
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    tomllib = None

from . import metrics as metrics_mod
from .scan import find_source_root, iter_python_files, scan

DEFAULT_METRICS = ".metrics/crap.edn"


@dataclass
class Config:
    root: Path
    src: str | None = None
    metrics: str = DEFAULT_METRICS
    layers: list[list[str]] = field(default_factory=list)
    threshold: float = 30.0
    exclude: list[str] = field(default_factory=list)
    title: str | None = None

    @classmethod
    def load(cls, root: Path, **overrides) -> "Config":
        cfg = cls(root=root.resolve())
        pyproject = root / "pyproject.toml"
        if tomllib and pyproject.exists():
            try:
                data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            section = data.get("tool", {}).get("umlscope", {})
            for key in ("src", "metrics", "layers", "threshold", "exclude", "title"):
                if key in section:
                    setattr(cfg, key, section[key])
            if not cfg.title:
                cfg.title = data.get("project", {}).get("name")
        for key, value in overrides.items():
            if value is not None:
                setattr(cfg, key, value)
        cfg.layers = [[layer] if isinstance(layer, str) else list(layer) for layer in cfg.layers]
        if not cfg.title:
            cfg.title = cfg.root.name
        return cfg

    @property
    def src_root(self) -> Path:
        return find_source_root(self.root, self.src)

    @property
    def metrics_path(self) -> Path:
        return (self.root / self.metrics).resolve()


def fingerprint(cfg: Config) -> str:
    """Cheap change detector: paths, sizes and mtimes of sources + metrics."""
    h = hashlib.sha1()
    for path in iter_python_files(cfg.src_root, set(cfg.exclude)):
        try:
            st = path.stat()
        except OSError:
            continue
        h.update(f"{path}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    mp = cfg.metrics_path
    if mp.exists():
        st = mp.stat()
        h.update(f"{mp}|{st.st_size}|{st.st_mtime_ns}".encode())
    h.update(repr((cfg.layers, cfg.threshold)).encode())
    return h.hexdigest()[:16]


def _common_prefix(ids: list[str]) -> str:
    if not ids:
        return ""
    split = [i.split(".") for i in ids]
    common = []
    for parts in zip(*split):
        if all(p == parts[0] for p in parts):
            common.append(parts[0])
        else:
            break
    # never make a non-package module the prefix
    while common and ".".join(common) in ids and len(ids) == 1:
        common.pop()
    return ".".join(common)


def _rank(mod_id: str, prefix: str, layers: list[list[str]]) -> int | None:
    rel = mod_id[len(prefix) + 1:] if prefix and mod_id.startswith(prefix + ".") else mod_id
    best, best_len = None, -1
    for idx, group in enumerate(layers):
        for entry in group:
            entry = entry[len(prefix) + 1:] if prefix and entry.startswith(prefix + ".") else entry
            if (rel == entry or rel.startswith(entry + ".")) and len(entry) > best_len:
                best, best_len = idx, len(entry)
    return best


def _band(crap: float | None, threshold: float) -> str:
    if crap is None:
        return "unknown"
    if crap <= 5:
        return "low"
    if crap <= threshold:
        return "medium"
    return "high"


def build(cfg: Config) -> dict:
    t0 = time.perf_counter()
    src_root = cfg.src_root
    modules, relations = scan(src_root, set(cfg.exclude))
    snap = metrics_mod.load(cfg.metrics_path, modules)
    ids = sorted(modules)
    prefix = _common_prefix(ids)

    # ---- tree -----------------------------------------------------------
    nodes: dict[str, dict] = {"": {"id": "", "name": cfg.title, "parent": None, "kind": "package",
                                  "children": [], "file": None}}

    def ensure(node_id: str, kind: str) -> dict:
        if node_id in nodes:
            if kind == "package":
                nodes[node_id]["kind"] = "package"
            return nodes[node_id]
        parent = node_id.rpartition(".")[0]
        ensure(parent, "package")
        node = {"id": node_id, "name": node_id.rpartition(".")[2], "parent": parent,
                "kind": kind, "children": [], "file": None}
        nodes[node_id] = node
        nodes[parent]["children"].append(node_id)
        return node

    hotspots = []
    for mod_id in ids:
        mod = modules[mod_id]
        node = ensure(mod_id, "package" if mod.is_package else "module")
        node["file"] = os.path.relpath(mod.path, cfg.root)
        node["error"] = mod.error
        node["ownDeps"] = sorted(mod.deps)

        def scored(fn, cls_name=""):
            s = snap.scores.get((mod_id, cls_name, fn.name))
            out = fn.to_json()
            if s:
                out.update(cc=s.cc, cov=s.cov, crap=s.crap)
            out["band"] = _band(s.crap if s else None, cfg.threshold)
            if s and s.crap is not None:
                hotspots.append({"module": mod_id, "cls": cls_name, "name": fn.name, "line": fn.line,
                                 "crap": s.crap, "cc": s.cc, "cov": s.cov, "band": out["band"]})
            return out

        node["functions"] = [scored(f) for f in mod.functions]
        node["classes"] = [{
            "name": c.name, "line": c.line, "end": c.end, "kind": c.kind,
            "bases": c.bases, "baseExprs": c.base_exprs,
            "methods": [scored(m, c.name) for m in c.methods],
        } for c in mod.classes]
        node["stereotype"] = _stereotype(mod)

    # ---- metric roll-up: parents take the worst of their children --------
    def roll(node_id: str) -> dict:
        node = nodes[node_id]
        members = list(node.get("functions", []))
        for c in node.get("classes", []):
            members.extend(c["methods"])
        crap_vals = [m["crap"] for m in members if m.get("crap") is not None]
        agg = {
            "worst": max(crap_vals) if crap_vals else None,
            "scored": len(crap_vals),
            "unscored": len(members) - len(crap_vals),
            "functions": len(members),
            "over": sum(1 for v in crap_vals if v > cfg.threshold),
            "modules": 1 if node["kind"] == "module" or node.get("file") else 0,
        }
        for child in node["children"]:
            c = roll(child)
            if c["worst"] is not None and (agg["worst"] is None or c["worst"] > agg["worst"]):
                agg["worst"] = c["worst"]
            for k in ("scored", "unscored", "functions", "over", "modules"):
                agg[k] += c[k]
        agg["band"] = _band(agg["worst"], cfg.threshold)
        node["metrics"] = agg
        node["children"].sort(key=lambda cid: (nodes[cid]["kind"] != "package", nodes[cid]["name"]))
        return agg

    roll("")

    # ---- leaf edges + dependency rule ------------------------------------
    edge_map: dict[tuple[str, str], dict] = {}
    for mod in modules.values():
        for dep in mod.deps:
            edge_map[(mod.id, dep)] = {"from": mod.id, "to": dep, "kinds": ["dependency"]}
    for rel in relations:
        a, b = rel["from"].split(":")[0], rel["to"].split(":")[0]
        if a == b:
            continue
        e = edge_map.setdefault((a, b), {"from": a, "to": b, "kinds": []})
        if rel["kind"] not in e["kinds"]:
            e["kinds"].append(rel["kind"])
    ranks = {}
    if cfg.layers:
        for mod_id in ids:
            ranks[mod_id] = _rank(mod_id, prefix, cfg.layers)
    edges = []
    for e in edge_map.values():
        ra, rb = ranks.get(e["from"]), ranks.get(e["to"])
        e["violation"] = bool(ra is not None and rb is not None and ra < rb and "dependency" in e["kinds"])
        edges.append(e)
    for mod_id, r in ranks.items():
        nodes[mod_id]["rank"] = r

    # one row per scored name: if/else platform branches define the same function twice
    seen, unique = set(), []
    for h in sorted(hotspots, key=lambda h: (-h["crap"], h["line"])):
        key = (h["module"], h["cls"], h["name"])
        if key not in seen:
            seen.add(key)
            unique.append(h)
    hotspots = unique
    start = ""
    while len(nodes[start]["children"]) == 1 and nodes[nodes[start]["children"][0]]["kind"] == "package":
        start = nodes[start]["children"][0]

    return {
        "project": cfg.title,
        "root": str(cfg.root),
        "src": os.path.relpath(src_root, cfg.root),
        "prefix": prefix,
        "start": start,
        "threshold": cfg.threshold,
        "layers": cfg.layers,
        "metrics": snap.to_json(),
        "nodes": nodes,
        "edges": sorted(edges, key=lambda e: (e["from"], e["to"])),
        "classEdges": relations,
        "hotspots": hotspots[:40],
        "violations": sum(1 for e in edges if e["violation"]),
        "parseErrors": [m.id for m in modules.values() if m.error],
        "scanMs": round((time.perf_counter() - t0) * 1000),
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _stereotype(mod) -> str | None:
    public = [c for c in mod.classes if "." not in c.name and not c.name.startswith("_")]
    if public and not mod.functions:
        if all(c.kind == "interface" for c in public):
            return "interface"
        if all(c.kind in ("interface", "abstract") for c in public):
            return "abstract"
    return None
