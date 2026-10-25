"""Command line: `umlscope [PATH]` serves the viewer; `umlscope export` writes JSON."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from pathlib import Path

from . import __version__
from .model import Config, build

EDITORS = ("cursor", "vscode", "pycharm", "none")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="umlscope",
        description="Drill-down architecture viewer for Python projects, colored by CRAP scores.",
    )
    p.add_argument("command", nargs="?", default="serve", choices=("serve", "export", "check"),
                   help="serve (default): open the viewer; export: print the model as JSON; "
                        "check: print a summary and exit non-zero on dependency-rule violations")
    p.add_argument("path", nargs="?", default=".", help="project root (default: current directory)")
    p.add_argument("--src", help="source root relative to the project (default: src/ if present, else root)")
    p.add_argument("--metrics", help="metrics snapshot (default: .metrics/crap.edn)")
    p.add_argument("--threshold", type=float, help="CRAP score treated as high risk (default: 30)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--editor", choices=EDITORS, default="cursor",
                   help="which editor 'open' links target (default: cursor; switchable in the page)")
    p.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    p.add_argument("--version", action="version", version=f"umlscope {__version__}")
    args = p.parse_args(argv)

    root = Path(args.path).expanduser()
    if not root.is_dir():
        p.error(f"not a directory: {root}")
    cfg = Config.load(root, src=args.src, metrics=args.metrics, threshold=args.threshold)

    if args.command == "export":
        json.dump(build(cfg), sys.stdout, indent=1)
        print()
        return 0
    if args.command == "check":
        return _check(cfg)

    from .server import serve

    try:
        httpd, state = serve(cfg, args.host, args.port, args.editor)
    except OSError as exc:
        print(f"umlscope: cannot listen on {args.host}:{args.port} ({exc}). Try --port.", file=sys.stderr)
        return 1
    _, model = state.current()
    url = f"http://{args.host}:{httpd.server_address[1]}/"
    m = model["metrics"]
    print(f"umlscope {__version__}: {model['project']} ({len(model['nodes']) - 1} nodes, "
          f"src={model['src']}, scanned in {model['scanMs']} ms)")
    if m["path"] and m["entries"]:
        print(f"  metrics: {m['matched']}/{m['entries']} crap entries placed"
              + (f", {m['unmatched_count']} unmatched" if m["unmatched_count"] else ""))
    else:
        print(f"  metrics: none at {cfg.metrics_path} (run crapper to color the diagram)")
    print(f"  serving {url}  (Ctrl-C to stop)")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        httpd.server_close()
    return 0


def _check(cfg: Config) -> int:
    model = build(cfg)
    root = model["nodes"][""]["metrics"]
    print(f"{model['project']}: {root['modules']} modules, {root['functions']} functions, "
          f"{root['scored']} scored, worst CRAP {root['worst']}, "
          f"{root['over']} over {model['threshold']:g}")
    bad = [e for e in model["edges"] if e["violation"]]
    for e in bad:
        print(f"  dependency-rule violation: {e['from']} -> {e['to']}")
    if model["metrics"]["unmatched_count"]:
        print(f"  {model['metrics']['unmatched_count']} metric entries could not be placed, e.g. "
              + ", ".join(model["metrics"]["unmatched"][:3]))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
