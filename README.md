# umlscope

A drill-down architecture viewer for Python projects, colored by CRAP score.

It reads your source tree and the `.metrics/crap.edn` snapshot that
[crapper](https://github.com/unclebob/crapper) writes, then serves a page in your
browser. The idea comes from Uncle Bob's
[uml-viewer](https://github.com/unclebob/uml-viewer); this is a Python-only
reimplementation with no JVM, no Clojure, and no runtime dependencies beyond the
Python standard library.

## Install

Python 3.11 or newer. From the folder containing this README (or the wheel):

```sh
uv tool install .            # or: pipx install .
# or from the wheel:
uv tool install umlscope-0.1.0-py3-none-any.whl
```

## Use

```sh
cd your-project
crapper                     # writes .metrics/crap.edn (optional, but it's what colors the boxes)
umlscope                    # scans, serves http://127.0.0.1:8765, opens your browser
```

Leave it running. When an agent edits code or re-runs crapper, the page picks up
the change within about two seconds, keeps your place, and outlines the packages
whose scores changed.

Other commands:

```sh
umlscope check              # summary; exits 1 if there are dependency-rule violations
umlscope export > model.json
umlscope --src lib --port 9000 --editor vscode
```

## Reading the diagram

- **One box is one module** (a `.py` file). `__init__.py` is its package.
  Packages have a double border; double-click one to open it. Esc goes up.
- **Fill color is the worst CRAP score inside the box**: green ≤ 5, ochre ≤ the
  threshold (30 by default), brick above it. A dashed outline means *not
  measured*: no crapper entry, or coverage not run. Missing data is never
  shown as healthy.
- **Arrows use UML notation.** Dashed with an open head: imports. Dashed with a
  hollow triangle: implements a `Protocol` or ABC. Solid with a hollow triangle:
  extends a concrete class. Red: breaks your layering rule. Hover an arrow to see
  the module pairs it bundles.
- **Busy levels switch to a risk grid.** Above 28 boxes at one level, every arrow
  at once is unreadable, so boxes are laid out riskiest-first and arrows show only
  for the selection. The toggle at top right switches layouts.
- **Click a box** for its UML class cards: each method with visibility (+/−) and
  its CRAP score. Click a method to see its code with the method highlighted, and
  open it in Cursor, VS Code or PyCharm (dropdown at top right).
- With nothing selected, the side panel lists the riskiest functions and every
  rule violation in the current package.

## Configure (optional)

In your project's `pyproject.toml`:

```toml
[tool.umlscope]
src = "src"                        # default: src/ if it exists, else the project root
metrics = ".metrics/crap.edn"      # .json with the same shape also works
threshold = 30
exclude = ["examples", "benchmarks"]
# Dependency rule: inner layers first. An import from an inner layer to an
# outer one is drawn red and counted by `umlscope check`.
layers = [["domain"], ["application", "services"], ["adapters", "api"]]
```

Layer names are paths below the top package (`domain`, or deeper:
`domain.billing`). Only import dependencies count as violations.

## How it matches crapper

crapper names Python entries by dotted module path (`pkg.mod`) or `pkg.mod.Class`,
plus the function name. umlscope matches those against the modules it scanned,
tolerating extra leading segments such as `src.`. Anything it cannot place is
counted in the status bar and listed in the side panel, so a stale snapshot or a
renamed function is visible instead of silently dropped. Both tools skip test
directories; if you point crapper at a subfolder, set `src` or `exclude` so the
two cover the same code.

Known edges: a class defined inside a function (crapper scores it as
`module.Class`) is reported as unplaced, because drawing it as a module-level
class would misstate the structure. Definitions inside module-level
`if`/`try`/`with` blocks (`if __name__ == "__main__":`, platform branches,
import fallbacks) are included.

## How it works

- `scan.py`: walks the tree with `ast` only, never importing your code. It
  resolves absolute, relative and `importlib.import_module("literal")` imports to
  project modules, and resolves base classes through aliases and re-exports
  (`from .model import Order` in an `__init__.py`).
- `metrics.py` plus a small EDN reader in `edn.py`: loads and joins the snapshot.
- `model.py`: builds the tree, rolls each package up to the worst of its
  children, and applies the layer rule.
- `server.py`: a standard-library HTTP server bound to 127.0.0.1. The source
  endpoint serves only files the scanner found.
- `static/index.html`: one page, using vendored Cytoscape.js and dagre (MIT), so
  it works offline.

Change detection polls file sizes and mtimes every 1.5 s, which keeps it
agent-agnostic: Cursor, Copilot, Claude Code and Kiro all just write files.

## Not yet

- Mutation scores (Bob's second color channel). The join is structured for it.
  It needs a sample of the mutation tool's output format.
- An MCP endpoint, so an agent can ask "what's the user looking at" or "what
  regressed".

## Develop

```sh
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```
