from __future__ import annotations

from pathlib import Path

import pytest

from eichhorn import schema
from eichhorn.config import load_config
from eichhorn.errors import ConfigError
from eichhorn.excludes import Excludes
from eichhorn.names import NameMapper, collision_name, escape
from eichhorn.yamlload import load_text


# ---------------------------------------------------------------- excludes
@pytest.mark.parametrize(
    ("pattern", "path", "is_dir", "expected"),
    [
        (".DS_Store", ".DS_Store", False, True),
        (".DS_Store", "a/b/.DS_Store", False, True),
        ("*.tmp", "x/y.tmp", False, True),
        ("*.tmp", "x/y.tmpl", False, False),
        ("/cache/", "cache", True, True),
        ("/cache/", "a/cache", True, False),
        ("cache/", "a/cache", True, True),
        ("cache/", "a/cache", False, False),  # trailing / = folders only
        ("*/cache/", "jan/cache", True, True),
        ("/appdata_*/preview/", "appdata_oc1/preview", True, True),
        ("/appdata_*/preview/", "x/appdata_oc1/preview", True, False),
        ("**/node_modules/", "a/b/node_modules", True, True),
        ("file?.txt", "file1.txt", False, True),
        ("file[!0-9].txt", "file1.txt", False, False),
        ("~$*", "~$Report.docx", False, True),
    ],
)
def test_excludes(pattern: str, path: str, is_dir: bool, expected: bool) -> None:
    assert Excludes([pattern]).excluded(path, is_dir) is expected


def test_rsync_filter_rules_are_rejected() -> None:
    assert schema.check_excludes(["+ keep/", "- drop/"])
    assert not schema.check_excludes(["*.tmp", "/cache/"])


# ---------------------------------------------------------------- names
def test_escape_windows_names() -> None:
    assert escape("a:b.txt", windows_rules=True) == "a%3Ab.txt"
    assert escape("100%:x", windows_rules=True) == "100%25%3Ax"
    assert escape("CON.txt", windows_rules=True) == "%43ON.txt"
    assert escape("trailing.", windows_rules=True) == "trailing%2E"
    assert escape("a:b.txt", windows_rules=False) == "a:b.txt"
    assert escape("normal name.jpg", windows_rules=True) == "normal name.jpg"


def test_case_collisions() -> None:
    m = NameMapper(windows_rules=False, case_sensitive=False)
    assert m.stored_name("d", "Photo.jpg") == "Photo.jpg"
    assert m.stored_name("d", "photo.jpg") == "photo (eichhorn-2).jpg"
    assert m.stored_name("other", "photo.jpg") == "photo.jpg"
    assert collision_name(".env", 2) == ".env (eichhorn-2)"


# ---------------------------------------------------------------- yaml + schema
def test_schema_reports_lines_and_typos() -> None:
    doc = load_text("description: x\nsources:\n  - name: Docs\n    pth: /a\n", Path("t.yaml"))
    problems = schema.validate(doc, schema.TARGET_SCHEMA)
    text = "\n".join(problems)
    assert "t.yaml:4: sources.0.pth: unknown key (did you mean 'path'?)" in text
    assert "missing required key 'path'" in text
    assert "'Docs' may only contain" in text


def test_yaml_boolean_surprise_is_explained() -> None:
    doc = load_text("description: no\nsources: []\n", Path("t.yaml"))
    problems = schema.validate(doc, schema.TARGET_SCHEMA)
    assert any("put it in quotes" in p for p in problems)


def test_duplicate_keys_are_an_error() -> None:
    with pytest.raises(ConfigError, match="duplicate key"):
        load_text("host: a\nhost: b\n", Path("t.yaml"))


def test_duplicate_source_names(tmp_path: Path) -> None:
    t = tmp_path / "cfg" / "targets"
    t.mkdir(parents=True)
    (t / "x.yaml").write_text("sources:\n  - {name: a, path: /a}\n  - {name: a, path: /b}\n")
    _, problems = load_config(tmp_path / "cfg", strict=False)
    assert any("duplicate name 'a'" in p for p in problems)


def test_relative_paths_are_rejected(tmp_path: Path) -> None:
    t = tmp_path / "cfg" / "targets"
    t.mkdir(parents=True)
    (t / "x.yaml").write_text("sources:\n  - {name: a, path: relative/dir}\n")
    _, problems = load_config(tmp_path / "cfg", strict=False)
    assert any("must be absolute" in p for p in problems)


def test_duration() -> None:
    assert schema.parse_duration("30d") == 30 * 86400
    assert schema.parse_duration("2w") == 14 * 86400


def test_output_is_utf8_even_on_a_legacy_code_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Windows pipes default to cp1252, which cannot encode → (regression test)."""
    import io
    import sys

    from eichhorn.cli import main

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)
    assert main(["--config", str(tmp_path / "cfg"), "config", "check"]) == 0
    print("docs → Backup-A …")
    sys.stdout.flush()
    assert "docs → Backup-A …" in raw.getvalue().decode("utf-8")


def test_targets_in_eichhorn_yaml_with_anchors(tmp_path: Path) -> None:
    """Targets may live in eichhorn.yaml; YAML anchors and x- keys help to share values."""
    cdir = tmp_path / "cfg"
    cdir.mkdir()
    (cdir / "eichhorn.yaml").write_text(
        """x-server: &server
  host: web1
  sudo: true

targets:
  site:
    <<: *server
    recipe: static-site
    with: {path: /srv/site}
  other:
    <<: *server
    sudo: false
    sources:
      - {name: a, path: /srv/a}
  me:
    recipe: eichhorn-config
"""
    )
    cfg, problems = load_config(cdir, strict=False)
    assert problems == []
    assert cfg.targets["site"].host == "web1" and cfg.targets["site"].sudo is True
    assert cfg.targets["other"].sudo is False  # written keys win over merged ones
    assert cfg.targets["site"].inline
    me = cfg.targets["me"]
    assert me.host == "local"
    assert me.sources[0].path == str(cdir.absolute())
    assert "/state/" in me.sources[0].exclude


def test_inline_target_errors_have_lines_and_conflicts(tmp_path: Path) -> None:
    cdir = tmp_path / "cfg"
    (cdir / "targets").mkdir(parents=True)
    (cdir / "eichhorn.yaml").write_text(
        "targets:\n  a:\n    sources:\n      - {name: x, pth: /x}\n  b:\n    sources: []\n"
        "    dumps: [{name: d, type: command, run: 'true'}]\n"
    )
    (cdir / "targets" / "b.yaml").write_text("sources:\n  - {name: y, path: /y}\n")
    _, problems = load_config(cdir, strict=False)
    text = "\n".join(problems)
    assert "eichhorn.yaml:4: sources.0.pth: unknown key" in text
    assert "target 'b' is also defined in eichhorn.yaml" in text
