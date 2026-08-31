#!/usr/bin/env python3
"""Build a self-service portal (Backstage) software template from template_config.yaml.

Usage:
    python3 build_template.py                          # uses template_config.yaml
    python3 build_template.py --config other.yaml      # a different template
    python3 build_template.py --data new_list.json     # override cascade data file

The config describes the whole template: identity, a form with any mix of
field types (cascade / select / text / textarea / number / integer /
boolean / password), the AAP job launch, and the run output (emitted with
YAML | block style). The generated file is ready for the portal to consume.
"""
import argparse
import os
import re
from collections import OrderedDict
from typing import Optional

# repo root = parent directory of generator/
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def repo_path(path: str) -> str:
    """Resolve a config path against the repo root unless it is absolute."""
    return path if os.path.isabs(path) else os.path.join(REPO_ROOT, path)

import yaml


# ── emit multi-line strings with | block style ───────────────────────────────
class TemplateDumper(yaml.SafeDumper):
    pass


def _str_presenter(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


TemplateDumper.add_representer(str, _str_presenter)


def var_name(key: str) -> str:
    """'server-config' -> 'server_config' (safe Ansible/Jinja variable name)."""
    return re.sub(r"[^0-9a-zA-Z_]", "_", key)


def load_yaml(path: str):
    with open(repo_path(path), encoding="utf-8") as fh:
        return yaml.safe_load(fh)  # parses JSON too


# ── field builders ───────────────────────────────────────────────────────────
def _nest(records, keys, levels, depth):
    """Recursively build the dependencies block for levels[depth:] .

    Returns (schema_for_this_level, dependencies_or_None) for the *next* key,
    grouping `records` by the key at `depth`.
    """
    key = keys[depth]
    grouped: "OrderedDict[str, list]" = OrderedDict()
    for rec in records:
        grouped.setdefault(rec[key], []).append(rec)

    var = var_name(key)
    title = levels[depth].get(
        "title", key.replace("-", " ").replace("_", " ").title()
    )
    schema = {"title": title, "type": "string", "enum": list(grouped.keys())}

    # last level: no further dependencies
    if depth == len(keys) - 1:
        return schema, None

    next_key = keys[depth + 1]
    next_var = var_name(next_key)
    branches = []
    for value, subset in grouped.items():
        child_schema, child_deps = _nest(subset, keys, levels, depth + 1)
        branch = {
            "properties": {var: {"enum": [value]}, next_var: child_schema},
            "required": [next_var],
        }
        if child_deps:                      # nest deeper levels inside this branch
            branch["dependencies"] = child_deps
        branches.append(branch)

    return schema, {var: {"oneOf": branches}}


def build_cascade(field: dict, data_override: Optional[str] = None):
    """Returns (properties, dependencies, required, [(param, extra_var)], stats).

    Supports any depth. Preferred config form:
        levels:
          - key: env
          - key: host
          - key: service
    Legacy 2-level form (parent_key/child_key) is still accepted.
    """
    data_file = data_override or field["data"]
    records = load_yaml(data_file)
    if not isinstance(records, list) or not records:
        raise SystemExit(f"{data_file} must contain a non-empty list of records")

    # ---- normalise config into a list of level dicts ------------------------
    if field.get("levels"):
        levels = field["levels"]
    else:  # legacy two-level config
        levels = [
            {"key": field["parent_key"], "title": field.get("parent_title"),
             "var": field.get("parent_var")},
            {"key": field["child_key"], "title": field.get("child_title"),
             "var": field.get("child_var")},
        ]
        levels = [{k: v for k, v in lv.items() if v is not None} for lv in levels]

    if len(levels) < 2:
        raise SystemExit("a cascade needs at least 2 levels")
    keys = [lv["key"] for lv in levels]

    for i, rec in enumerate(records):
        missing = [k for k in keys if not isinstance(rec, dict) or k not in rec]
        if missing:
            raise SystemExit(
                f"record {i} in {data_file} is missing {missing}: {rec}"
            )

    first_schema, dependencies = _nest(records, keys, levels, 0)
    first_var = var_name(keys[0])
    properties = {first_var: first_schema}

    mapping = [(var_name(lv["key"]), lv.get("var", var_name(lv["key"]))) for lv in levels]

    counts = [len({rec[k] for rec in records}) for k in keys]
    stats = " -> ".join(f"{c} {var_name(k)}" for c, k in zip(counts, keys))
    return properties, dependencies, [first_var], mapping, stats


def build_simple(field: dict):
    """Non-cascade field -> (name, schema, required?)."""
    ftype = field["type"]
    name = field["name"]
    schema: dict = {"title": field.get("title", name.replace("_", " ").title())}
    if field.get("description"):
        schema["description"] = field["description"]

    if ftype in ("text", "password", "textarea"):
        schema["type"] = "string"
        if ftype == "password":
            schema["ui:widget"] = "password"
        if ftype == "textarea":
            schema["ui:widget"] = "textarea"
    elif ftype in ("number", "integer"):
        schema["type"] = ftype
        for k in ("minimum", "maximum"):
            if field.get(k) is not None:
                schema[k] = field[k]
    elif ftype == "boolean":
        schema["type"] = "boolean"
    elif ftype == "select":
        schema["type"] = "string"
        opts = field["options"]
        if opts and isinstance(opts[0], dict):
            schema["enum"] = [o["value"] for o in opts]
            schema["enumNames"] = [o.get("label", o["value"]) for o in opts]
        else:
            schema["enum"] = list(opts)
    else:
        raise SystemExit(f"unknown field type '{ftype}' for field '{name}'")

    if field.get("default") is not None:
        schema["default"] = field["default"]
    # optional `var:` renames the extra var without touching the form field name
    extra_var = field.get("var", name)
    return name, schema, bool(field.get("required", False)), extra_var


# ── main assembly ────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="template_config.yaml")
    ap.add_argument("--data", default=None,
                    help="override the cascade field's data file (e.g. a new list)")
    ap.add_argument("--out", default=None,
                    help="override the config's output_file for this run")
    args = ap.parse_args()

    cfg = load_yaml(args.config)
    tpl, form, job = cfg["template"], cfg["form"], cfg["job"]

    properties: dict = {}
    dependencies: dict = {}
    required: list = []
    var_map: list = []          # [(form parameter name, extra var name)]
    cascade_stats = []

    for field in form["fields"]:
        if field["type"] == "cascade":
            props, deps, req, mapping, stats = build_cascade(field, args.data)
            properties.update(props)
            dependencies.update(deps)
            required += req
            var_map += mapping
            cascade_stats.append(stats)
        else:
            name, schema, is_req, extra_var = build_simple(field)
            properties[name] = schema
            var_map.append((name, extra_var))
            if is_req:
                required.append(name)

    form_page: dict = {
        "title": form.get("page_title", tpl["title"]),
        "properties": properties,
    }
    if required:
        form_page["required"] = required
    if dependencies:
        form_page["dependencies"] = dependencies

    auth_page = {
        "title": "Authentication",
        "required": ["token"],
        "properties": {
            "token": {
                "title": "Token",
                "type": "string",
                "ui:field": "AAPTokenField",
                "ui:widget": "hidden",
            }
        },
    }

    # each form field becomes an extra var, named by its `var:` (default: field name);
    # fixed extra_vars from the config merge in last
    extra_variables = {
        extra_var: "${{ parameters.%s }}" % param for param, extra_var in var_map
    }
    extra_variables.update(job.get("extra_vars") or {})

    launch_values = {"template": job["template"], "extraVariables": extra_variables}
    if job.get("organization"):
        launch_values["organization"] = job["organization"]

    out_cfg = cfg.get("output") or {}
    template = {
        "apiVersion": "scaffolder.backstage.io/v1beta3",
        "kind": "Template",
        "metadata": {
            "namespace": "default",
            "name": tpl["name"],
            "title": tpl["title"],
            "description": tpl.get("description", ""),
            "tags": tpl.get("tags", []),
        },
        "spec": {
            "type": "service",
            "parameters": [form_page, auth_page],
            "steps": [
                {
                    "id": "launch-job",
                    "name": job["template"],
                    "action": "rhaap:launch-job-template",
                    "input": {
                        "token": "${{ secrets.aapToken or parameters.token }}",
                        "values": launch_values,
                    },
                }
            ],
            "output": {
                "text": [
                    {
                        "title": out_cfg.get("title", "Request submitted"),
                        "content": out_cfg.get(
                            "content",
                            "**Job ID:** ${{ steps['launch-job'].output.data.id }}\n"
                            "**Status:** ${{ steps['launch-job'].output.data.status }}\n",
                        ),
                    }
                ]
            },
        },
    }

    out_file = args.out or cfg.get("output_file", "sample.yaml")
    header = (
        "# ── GENERATED FILE — DO NOT EDIT ─────────────────────────────\n"
        f"# Built by build_template.py from {args.config}"
        + (f" (data: {args.data})" if args.data else "")
        + "\n"
        + (f"# cascade: {'; '.join(cascade_stats)}\n" if cascade_stats else "")
        + "# ─────────────────────────────────────────────────────────────\n"
    )
    body = yaml.dump(template, Dumper=TemplateDumper, sort_keys=False,
                     width=100, allow_unicode=True)
    os.makedirs(os.path.dirname(repo_path(out_file)) or ".", exist_ok=True)
    with open(repo_path(out_file), "w", encoding="utf-8") as fh:
        fh.write(header + body)

    renamed = [f"{p} -> {v}" for p, v in var_map if p != v]
    print(f"wrote {out_file}: {len(var_map)} form fields "
          f"({'; '.join(cascade_stats) if cascade_stats else 'no cascade'})")
    if renamed:
        print("  renamed extra vars: " + ", ".join(renamed))


if __name__ == "__main__":
    main()
