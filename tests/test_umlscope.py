from pathlib import Path

import pytest

from umlscope import edn
from umlscope.model import Config, build, _common_prefix
from umlscope.scan import scan

SHOP = Path(__file__).parent / "fixtures" / "shop"


@pytest.fixture(scope="module")
def model():
    return build(Config.load(SHOP))


def test_edn_reader():
    data = edn.loads('{:a [1 2.5 "x\\"y" nil true] :b #{:k} :c {:d -3} #_ :skip}  ; trailing comment')
    assert data == {"a": [1, 2.5, 'x"y', None, True], "b": {"k"}, "c": {"d": -3}}


def test_edn_rejects_garbage():
    with pytest.raises(edn.EdnError):
        edn.loads("{:a 1")


def test_scan_finds_modules_and_skips_tests():
    modules, _ = scan(SHOP / "src")
    assert set(modules) == {"shop", "shop.domain", "shop.domain.model", "shop.domain.pricing",
                            "shop.app.checkout", "shop.adapters", "shop.adapters.db",
                            "shop.adapters.broken"}
    assert modules["shop.adapters.broken"].error
    assert modules["shop.domain"].is_package


def test_relative_and_dynamic_imports_resolve():
    modules, _ = scan(SHOP / "src")
    assert modules["shop.app.checkout"].deps == {"shop.domain", "shop.domain.pricing", "shop.adapters.db"}
    assert modules["shop.domain"].deps == {"shop.domain.model"}


def test_bases_resolve_through_reexports_and_module_aliases():
    _, relations = scan(SHOP / "src")
    pairs = {(r["from"], r["to"], r["kind"]) for r in relations}
    assert ("shop.adapters.db:SqlRepository", "shop.domain.model:Repository", "implements") in pairs
    assert ("shop.adapters.db:Cached", "shop.domain.model:Order", "inheritance") in pairs


def test_metrics_join_with_prefix_and_nested_class(model):
    m = model["metrics"]
    assert m["entries"] == 6 and m["matched"] == 5
    assert m["unmatched"] == ["shop.gone/ghost"]
    order = next(c for c in model["nodes"]["shop.domain.model"]["classes"] if c["name"] == "Order")
    total = next(f for f in order["methods"] if f["name"] == "total")
    assert (total["cc"], total["cov"], total["crap"], total["band"]) == (4, 0.0, 20.0, "medium")
    line = next(c for c in model["nodes"]["shop.domain.model"]["classes"] if c["name"] == "Order.Line")
    assert line["methods"][0]["crap"] == 1.0


def test_unmeasured_coverage_is_unknown_not_green(model):
    fn = model["nodes"]["shop.app.checkout"]["functions"][0]
    assert fn["band"] == "unknown" and fn.get("crap") is None


def test_rollup_takes_worst_child(model):
    assert model["nodes"]["shop.adapters"]["metrics"]["worst"] == 117.1
    assert model["nodes"]["shop"]["metrics"]["band"] == "high"
    assert model["nodes"][""]["metrics"]["over"] == 1


def test_dependency_rule(model):
    bad = {(e["from"], e["to"]) for e in model["edges"] if e["violation"]}
    # domain -> adapters (static import) and app -> adapters (importlib) both point outward
    assert bad == {("shop.domain.pricing", "shop.adapters.db"), ("shop.app.checkout", "shop.adapters.db")}
    assert model["violations"] == 2
    # outer -> inner is allowed
    assert not any(e["violation"] for e in model["edges"] if e["from"] == "shop.adapters.db")


def test_start_skips_single_child_chain(model):
    assert model["start"] == "shop"
    assert model["prefix"] == "shop"


def test_common_prefix():
    assert _common_prefix(["a.b", "a.c"]) == "a"
    assert _common_prefix(["a", "b"]) == ""
    assert _common_prefix(["solo"]) == ""


def test_hotspots_sorted(model):
    craps = [h["crap"] for h in model["hotspots"]]
    assert craps == sorted(craps, reverse=True) and craps[0] == 117.1


def test_definitions_under_module_level_guards(tmp_path):
    pkg = tmp_path / "src" / "demo"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "m.py").write_text(
        "import sys\n"
        "try:\n    import fast\nexcept ImportError:\n    def fallback(): pass\n"
        "class C:\n    if sys.version_info >= (3, 12):\n        def new(self): pass\n"
        "    def plain(self):\n        def inner(): pass\n"
        "if __name__ == '__main__':\n    class Demo:\n        def run(self): pass\n"
    )
    modules, _ = scan(tmp_path / "src")
    m = modules["demo.m"]
    assert [f.name for f in m.functions] == ["fallback"]
    classes = {c.name: [x.name for x in c.methods] for c in m.classes}
    assert classes == {"C": ["new", "plain"], "Demo": ["run"]}
