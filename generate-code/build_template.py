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
import re
from collections import OrderedDict
from typing import Optional

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
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)  # parses JSON too


# ── field builders ───────────────────────────────────────────────────────────
def build_cascade(field: dict, data_override: Optional[str] = None):
    """Returns (properties, dependencies, required, var_names)."""
    data_file = data_override or field["data"]
    records = load_yaml(data_file)
    if not isinstance(records, list) or not records:
        raise SystemExit(f"{data_file} must contain a non-empty list of records")

    p_key, c_key = field["parent_key"], field["child_key"]
    grouped: "OrderedDict[str, list[str]]" = OrderedDict()
    for i, rec in enumerate(records):
        try:
            parent, child = rec[p_key], rec[c_key]
        except (TypeError, KeyError):
            raise SystemExit(f"record {i} in {data_file} is missing '{p_key}' or '{c_key}': {rec}")
        grouped.setdefault(parent, [])
        if child not in grouped[parent]:
            grouped[parent].append(child)

    p_var, c_var = var_name(p_key), var_name(c_key)
    p_title = field.get("parent_title", p_key.replace("-", " ").replace("_", " ").title())
    c_title = field.get("child_title", c_key.replace("-", " ").replace("_", " ").title())

    properties = {p_var: {"title": p_title, "type": "string", "enum": list(grouped.keys())}}
    branches = [
        {
            "properties": {
                p_var: {"enum": [parent]},
                c_var: {"title": c_title, "type": "string", "enum": children},
            },
            "required": [c_var],
        }
        for parent, children in grouped.items()
    ]
    dependencies = {p_var: {"oneOf": branches}}
    stats = f"{len(grouped)} {p_var} -> {sum(len(v) for v in grouped.values())} {c_var}"
    return properties, dependencies, [p_var], [p_var, c_var], stats


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
    return name, schema, bool(field.get("required", False))


# ── main assembly ────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="template_config.yaml")
    ap.add_argument("--data", default=None,
                    help="override the cascade field's data file (e.g. a new list)")
    args = ap.parse_args()

    cfg = load_yaml(args.config)
    tpl, form, job = cfg["template"], cfg["form"], cfg["job"]

    properties: dict = {}
    dependencies: dict = {}
    required: list = []
    all_vars: list = []
    cascade_stats = []

    for field in form["fields"]:
        if field["type"] == "cascade":
            props, deps, req, vars_, stats = build_cascade(field, args.data)
            properties.update(props)
            dependencies.update(deps)
            required += req
            all_vars += vars_
            cascade_stats.append(stats)
        else:
            name, schema, is_req = build_simple(field)
            properties[name] = schema
            all_vars.append(name)
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

    # every form field becomes an extra var; fixed extra_vars from config merge in
    extra_variables = {v: "${{ parameters.%s }}" % v for v in all_vars}
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

    out_file = cfg.get("output_file", "sample.yaml")
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
    with open(out_file, "w", encoding="utf-8") as fh:
        fh.write(header + body)

    print(f"wrote {out_file}: {len(all_vars)} form fields "
          f"({'; '.join(cascade_stats) if cascade_stats else 'no cascade'})")


if __name__ == "__main__":
    main()
