"""Static scan of a Python source tree.

Produces one record per module (a .py file; ``__init__.py`` is the package
itself) with its classes, methods, top-level functions, and the project
modules it imports. Nothing is imported or executed: everything comes from
``ast``, so scanning a broken or half-written tree still works.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "env", ".env",
    "build", "dist", "node_modules", "site-packages", ".tox", ".nox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "target", ".metrics",
    "tests", "test", "spec", ".idea", ".vscode", ".eggs", "htmlcov",
}
SKIP_FILES = {"setup.py", "conftest.py", "noxfile.py"}


def is_test_file(name: str) -> bool:
    return name.startswith("test_") or name.endswith("_test.py")


@dataclass
class Func:
    name: str
    line: int
    end: int
    private: bool

    def to_json(self):
        return {"name": self.name, "line": self.line, "end": self.end, "private": self.private}


@dataclass
class Cls:
    name: str          # qualified inside the module: Outer.Inner
    line: int
    end: int
    kind: str          # class | interface | abstract
    base_exprs: list[str]
    methods: list[Func] = field(default_factory=list)
    bases: list[dict] = field(default_factory=list)  # resolved: {target, kind}


@dataclass
class Module:
    id: str
    path: Path         # absolute
    is_package: bool
    classes: list[Cls] = field(default_factory=list)
    functions: list[Func] = field(default_factory=list)
    imports: set[str] = field(default_factory=set)        # raw dotted targets
    aliases: dict[str, str] = field(default_factory=dict)  # local name -> dotted target
    deps: set[str] = field(default_factory=set)           # resolved module ids
    error: str | None = None

    @property
    def package(self) -> str:
        if self.is_package:
            return self.id
        return self.id.rpartition(".")[0]


# --------------------------------------------------------------------------
# discovery

def find_source_root(root: Path, configured: str | None = None) -> Path:
    if configured:
        return (root / configured).resolve()
    src = root / "src"
    if src.is_dir() and any(p.suffix == ".py" or (p / "__init__.py").exists() or p.is_dir()
                            for p in src.iterdir() if not p.name.startswith(".")):
        return src.resolve()
    return root.resolve()


def iter_python_files(src_root: Path, extra_skip: set[str] = frozenset()):
    for dirpath, dirnames, filenames in os.walk(src_root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and d not in extra_skip and not d.startswith(".")
            and not d.endswith(".egg-info")
        )
        for name in sorted(filenames):
            if name.endswith(".py") and name not in SKIP_FILES and not is_test_file(name):
                yield Path(dirpath) / name


def module_id_for(path: Path, src_root: Path) -> tuple[str, bool]:
    rel = path.relative_to(src_root).with_suffix("")
    parts = list(rel.parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts = parts[:-1]
    return ".".join(parts), is_package


# --------------------------------------------------------------------------
# per-file extraction

def _dotted(expr: ast.AST) -> str | None:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        head = _dotted(expr.value)
        return f"{head}.{expr.attr}" if head else None
    if isinstance(expr, ast.Subscript):  # Generic[T], Protocol[T]
        return _dotted(expr.value)
    if isinstance(expr, ast.Call):        # NamedTuple("X", ...) etc.
        return None
    return None


_INTERFACE_BASES = {"Protocol", "typing.Protocol", "typing_extensions.Protocol"}
_ABSTRACT_BASES = {"ABC", "abc.ABC"}


def _class_kind(node: ast.ClassDef, base_exprs: list[str]) -> str:
    if any(b in _INTERFACE_BASES for b in base_exprs):
        return "interface"
    if any(b in _ABSTRACT_BASES for b in base_exprs):
        return "abstract"
    for kw in node.keywords:
        if kw.arg == "metaclass" and (_dotted(kw.value) or "").endswith("ABCMeta"):
            return "abstract"
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in item.decorator_list:
                if (_dotted(dec) or "").endswith("abstractmethod"):
                    return "abstract"
    return "class"


def _flatten(body):
    """Statements at this level, looking through if/try/with blocks.

    Definitions guarded by `if __name__ == "__main__":`, `if TYPE_CHECKING:`,
    a version check, or a try/except import fallback still belong to this
    level (crapper scores them), but nothing inside a function does.
    """
    for node in body:
        if isinstance(node, ast.If):
            yield from _flatten(node.body)
            yield from _flatten(node.orelse)
        elif isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            yield from _flatten(node.body)
            for h in node.handlers:
                yield from _flatten(h.body)
            yield from _flatten(node.orelse)
            yield from _flatten(node.finalbody)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            yield from _flatten(node.body)
        else:
            yield node


def _func(node) -> Func:
    name = node.name
    private = name.startswith("_") and not (name.startswith("__") and name.endswith("__"))
    return Func(name, node.lineno, getattr(node, "end_lineno", node.lineno), private)


def _collect_classes(body, prefix: str, out: list[Cls]):
    for node in _flatten(body):
        if isinstance(node, ast.ClassDef):
            qual = f"{prefix}{node.name}"
            base_exprs = [b for b in (_dotted(e) for e in node.bases) if b]
            cls = Cls(qual, node.lineno, getattr(node, "end_lineno", node.lineno),
                      _class_kind(node, base_exprs), base_exprs)
            for item in _flatten(node.body):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    cls.methods.append(_func(item))
            out.append(cls)
            _collect_classes(node.body, f"{qual}.", out)


def parse_module(mod: Module) -> None:
    try:
        source = mod.path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(mod.path))
    except (SyntaxError, ValueError) as exc:
        mod.error = f"{type(exc).__name__}: {exc}"
        return

    for node in _flatten(tree.body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            mod.functions.append(_func(node))
    _collect_classes(tree.body, "", mod.classes)

    pkg_parts = mod.package.split(".") if mod.package else []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mod.imports.add(a.name)
                if a.asname:
                    mod.aliases[a.asname] = a.name
                else:
                    head = a.name.split(".")[0]
                    mod.aliases.setdefault(head, head)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = len(pkg_parts) - (node.level - 1)
                if keep < 0:
                    continue  # relative import escaping the source root
                base_parts = pkg_parts[:keep]
                if node.module:
                    base_parts = base_parts + node.module.split(".")
                base = ".".join(base_parts)
            else:
                base = node.module or ""
            if not base:
                for a in node.names:
                    mod.imports.add(a.name)
                    mod.aliases[a.asname or a.name] = a.name
                continue
            mod.imports.add(base)
            for a in node.names:
                if a.name == "*":
                    continue
                target = f"{base}.{a.name}"
                mod.imports.add(target)
                mod.aliases[a.asname or a.name] = target
        elif isinstance(node, ast.Call):
            # importlib.import_module("literal.module")
            fn = _dotted(node.func) or ""
            if fn.endswith("import_module") and node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str) and not node.args[0].value.startswith("."):
                mod.imports.add(node.args[0].value)


# --------------------------------------------------------------------------
# whole-project resolution

def _longest_module(dotted: str, modules: dict[str, Module]) -> str | None:
    parts = dotted.split(".")
    for k in range(len(parts), 0, -1):
        cand = ".".join(parts[:k])
        if cand in modules:
            return cand
    return None


def _resolve_class(qual: str, modules: dict[str, Module], depth: int = 0) -> tuple[str, Cls] | None:
    """Resolve 'pkg.mod.Class' (possibly via re-exports) to its defining module and class."""
    if depth > 6:
        return None
    mod_id = _longest_module(qual, modules)
    if mod_id is None:
        return None
    rest = qual[len(mod_id) + 1:] if len(qual) > len(mod_id) else ""
    if not rest:
        return None
    mod = modules[mod_id]
    for c in mod.classes:
        if c.name == rest:
            return mod_id, c
    head, _, tail = rest.partition(".")
    if head in mod.aliases:  # re-export: from .base import Base
        target = mod.aliases[head] + (f".{tail}" if tail else "")
        if target != qual:
            return _resolve_class(target, modules, depth + 1)
    return None


def resolve(modules: dict[str, Module]) -> list[dict]:
    """Resolve imports to project modules and class bases to project classes.

    Returns class-level relations: [{from, to, kind}] with qualified names
    'module:Class'.
    """
    for mod in modules.values():
        for target in mod.imports:
            dep = _longest_module(target, modules)
            if dep and dep != mod.id:
                mod.deps.add(dep)

    relations = []
    for mod in modules.values():
        local = {c.name for c in mod.classes}
        for cls in mod.classes:
            for expr in cls.base_exprs:
                head, _, tail = expr.partition(".")
                if expr in local:
                    qual = f"{mod.id}.{expr}"
                elif head in mod.aliases:
                    qual = mod.aliases[head] + (f".{tail}" if tail else "")
                else:
                    continue
                hit = _resolve_class(qual, modules)
                if not hit:
                    continue
                target_mod, target_cls = hit
                kind = "implements" if target_cls.kind in ("interface", "abstract") else "inheritance"
                cls.bases.append({"module": target_mod, "cls": target_cls.name, "kind": kind})
                relations.append({
                    "from": f"{mod.id}:{cls.name}",
                    "to": f"{target_mod}:{target_cls.name}",
                    "kind": kind,
                })
    return relations


def scan(src_root: Path, extra_skip: set[str] = frozenset()) -> tuple[dict[str, Module], list[dict]]:
    modules: dict[str, Module] = {}
    for path in iter_python_files(src_root, extra_skip):
        mod_id, is_pkg = module_id_for(path, src_root)
        if not mod_id:
            continue  # __init__.py at the source root itself
        mod = Module(mod_id, path, is_pkg)
        parse_module(mod)
        modules[mod_id] = mod
    relations = resolve(modules)
    return modules, relations
