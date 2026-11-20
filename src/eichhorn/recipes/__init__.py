"""Recipes: templates that fill in a target for a known app (spec 4.6).

A recipe is a YAML file with `description`, `params` and a `target` template.
Inside the template:

- "${param}" alone is replaced by the parameter's value (keeping its type);
  inside a longer string it is replaced by its text.
- A list item `{value: X, when: param}` becomes X if the parameter is set and
  true (`when: "!param"` for the opposite); otherwise it is dropped.
- A mapping in a list with a `when` key is kept (without `when`) or dropped.
- A mapping in a list with `foreach: param` is repeated for every element of
  a list parameter, with ${item} and ${index} (1, 2, …).

Built-in recipes live next to this file; users can add their own in
<config>/recipes/<name>.yaml.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from eichhorn.yamlload import load_file, load_text

PLACEHOLDER = re.compile(r"\$\{([a-z_][a-z0-9_]*)\}")


@dataclass
class Param:
    name: str
    doc: str
    required: bool
    default: Any
    type: str  # str | bool | list


@dataclass
class Recipe:
    name: str
    description: str
    params: dict[str, Param]
    template: dict[str, Any]
    origin: str


def _from_data(name: str, data: dict[str, Any], origin: str) -> Recipe:
    params = {}
    for pname, spec in (data.get("params") or {}).items():
        spec = spec or {}
        default = spec.get("default")
        ptype = spec.get("type") or (
            "bool" if isinstance(default, bool) else "list" if isinstance(default, list) else "str"
        )
        params[pname] = Param(
            pname, spec.get("doc", ""), bool(spec.get("required")), default, ptype
        )
    return Recipe(name, data.get("description", ""), params, data.get("target") or {}, origin)


def builtin_names() -> list[str]:
    return sorted(
        p.name.removesuffix(".yaml")
        for p in resources.files(__package__).iterdir()
        if p.name.endswith(".yaml")
    )


def load(name: str, config_dir: Path | None = None) -> Recipe:
    if config_dir is not None:
        user = config_dir / "recipes" / f"{name}.yaml"
        if user.is_file():
            return _from_data(name, load_file(user).data or {}, str(user))
    res = resources.files(__package__) / f"{name}.yaml"
    if not res.is_file():
        known = ", ".join(all_names(config_dir))
        raise KeyError(f"unknown recipe {name!r} (known: {known})")
    data = load_text(res.read_text(encoding="utf-8"), Path(f"recipe:{name}")).data or {}
    return _from_data(name, data, "built-in")


def all_names(config_dir: Path | None = None) -> list[str]:
    names = set(builtin_names())
    if config_dir is not None and (config_dir / "recipes").is_dir():
        names |= {p.stem for p in (config_dir / "recipes").glob("*.yaml")}
    return sorted(names)


def check_params(recipe: Recipe, given: dict[str, Any]) -> list[str]:
    problems = []
    for key in given:
        if key not in recipe.params:
            problems.append(
                f"recipe {recipe.name!r} has no parameter {key!r} "
                f"(parameters: {', '.join(recipe.params)})"
            )
    for p in recipe.params.values():
        value = given.get(p.name, p.default)
        if p.required and value in (None, ""):
            problems.append(f"recipe {recipe.name!r} needs the parameter {p.name!r}: {p.doc}")
            continue
        if value is None:
            continue
        if p.type == "bool" and not isinstance(value, bool):
            problems.append(f"parameter {p.name!r} must be true or false, got {value!r}")
        elif p.type == "list" and not isinstance(value, list):
            problems.append(f"parameter {p.name!r} must be a list, got {value!r}")
        elif p.type == "str" and not isinstance(value, str):
            problems.append(f"parameter {p.name!r} must be a string, got {value!r}")
    return problems


def _subst(value: Any, params: dict[str, Any]) -> Any:
    if isinstance(value, str):
        m = PLACEHOLDER.fullmatch(value)
        if m:
            return params.get(m.group(1))
        return PLACEHOLDER.sub(
            lambda m: "" if params.get(m.group(1)) is None else str(params.get(m.group(1))), value
        )
    if isinstance(value, dict):
        return {k: _subst(v, params) for k, v in value.items()}
    if isinstance(value, list):
        out: list[Any] = []
        for item in value:
            if isinstance(item, dict) and "foreach" in item:
                values = params.get(item["foreach"]) or []
                body = {k: v for k, v in item.items() if k != "foreach"}
                for i, v in enumerate(values, start=1):
                    out.append(_subst(body, {**params, "item": v, "index": str(i)}))
                continue
            if isinstance(item, dict) and "when" in item:
                cond = str(item["when"])
                negate = cond.startswith("!")
                flag = bool(params.get(cond.lstrip("!")))
                if flag == negate:
                    continue
                if set(item) == {"value", "when"}:
                    out.append(_subst(item["value"], params))
                else:
                    out.append(_subst({k: v for k, v in item.items() if k != "when"}, params))
                continue
            out.append(_subst(item, params))
        return out
    return value


def _exclude_nested(sources: list[dict[str, Any]]) -> None:
    """If one source lies inside another, exclude it there (no double backup)."""
    for outer in sources:
        base = str(outer.get("path", "")).rstrip("/")
        for inner in sources:
            if inner is outer:
                continue
            path = str(inner.get("path", "")).rstrip("/")
            if base and path.startswith(base + "/"):
                rel = posixpath.relpath(path, base)
                pattern = f"/{rel}/"
                excludes = outer.setdefault("exclude", [])
                if pattern not in excludes:
                    excludes.append(pattern)


def expand(
    recipe: Recipe, given: dict[str, Any], variables: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Fill in the template. `variables` are provided by Eichhorn itself, such as
    ${eichhorn_config} (the configuration folder)."""
    params = dict(variables or {})
    params.update({p.name: p.default for p in recipe.params.values()})
    params.update(given)
    data: dict[str, Any] = _subst(recipe.template, params)
    _exclude_nested(data.get("sources") or [])
    return data


def merge(recipe_data: dict[str, Any], own: dict[str, Any]) -> dict[str, Any]:
    """Combine a recipe's result with the target file's own keys (spec 4.6).

    Nested sources are excluded from the outer one again afterwards, so that
    replacing a recipe's source with an own one (e.g. to add excludes) does not
    make a folder be backed up twice."""
    out = dict(recipe_data)
    for key, value in own.items():
        if key in ("recipe", "with"):
            continue
        if key in ("sources", "dumps") and isinstance(value, list):
            mine = {item.get("name"): item for item in value if isinstance(item, dict)}
            merged = [mine.pop(item.get("name"), item) for item in out.get(key) or []]
            out[key] = merged + list(mine.values())
        elif key == "hooks" and isinstance(value, dict):
            hooks = {k: list(v) for k, v in (out.get("hooks") or {}).items()}
            for phase, cmds in value.items():
                hooks[phase] = hooks.get(phase, []) + list(cmds or [])
            out["hooks"] = hooks
        else:
            out[key] = value
    if own.get("sources"):
        out["sources"] = [dict(s) for s in out.get("sources") or []]
        for s in out["sources"]:
            s["exclude"] = list(s.get("exclude") or [])
        _exclude_nested(out["sources"])
    return out
