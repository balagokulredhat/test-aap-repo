#!/usr/bin/env python3
"""Generate a cascading Backstage software template from a FLAT list of pairs.

Input: JSON or YAML file containing a list of records, e.g.
    [ {"host": "SwiftNode01", "server-config": "Srv01_Swift"}, ... ]
The generator groups children under each parent and emits the full
dependencies/oneOf cascade — no manual branch writing.

Usage:
  python3 generate_cascade.py --data host_configs.json \
      --parent host --child server-config \
      --name server-config-request --title "Server Config Request" \
      --job-template "Demo Job Template" --out sample.yaml

Only --data is required; everything else has sensible defaults.
"""
import argparse
import re
from collections import OrderedDict

import yaml


def var_name(key: str) -> str:
    """A key like 'server-config' becomes a safe Ansible var: server_config."""
    return re.sub(r"[^0-9a-zA-Z_]", "_", key)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="JSON/YAML file with the flat pair list")
    ap.add_argument("--parent", default="host", help="key used for the first dropdown")
    ap.add_argument("--child", default="server-config", help="key for the dependent dropdown")
    ap.add_argument("--name", default="server-config-request", help="catalog entity name")
    ap.add_argument("--title", default="Server Config Request")
    ap.add_argument("--description", default="Pick a host, then one of its server configs.")
    ap.add_argument("--job-template", default="Demo Job Template")
    ap.add_argument("--organization", default=None)
    ap.add_argument("--out", default="sample.yaml")
    args = ap.parse_args()

    with open(args.data, encoding="utf-8") as fh:
        records = yaml.safe_load(fh)  # parses JSON too

    if not isinstance(records, list):
        raise SystemExit(f"{args.data} must contain a list of records")

    # ---- group children under each parent (insertion order, de-duplicated) ----
    grouped: "OrderedDict[str, list[str]]" = OrderedDict()
    for i, rec in enumerate(records):
        try:
            parent, child = rec[args.parent], rec[args.child]
        except (TypeError, KeyError):
            raise SystemExit(f"record {i} is missing '{args.parent}' or '{args.child}': {rec}")
        grouped.setdefault(parent, [])
        if child not in grouped[parent]:
            grouped[parent].append(child)

    p_var, c_var = var_name(args.parent), var_name(args.child)
    p_title = args.parent.replace("-", " ").replace("_", " ").title()
    c_title = args.child.replace("-", " ").replace("_", " ").title()

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

    form_page = {
        "title": args.title,
        "required": [p_var],
        "properties": {
            p_var: {
                "title": p_title,
                "type": "string",
                "enum": list(grouped.keys()),
            }
        },
        "dependencies": {p_var: {"oneOf": branches}},
    }

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

    launch_values = {
        "template": args.job_template,
        "extraVariables": {
            p_var: "${{ parameters.%s }}" % p_var,
            c_var: "${{ parameters.%s }}" % c_var,
        },
    }
    if args.organization:
        launch_values["organization"] = args.organization

    template = {
        "apiVersion": "scaffolder.backstage.io/v1beta3",
        "kind": "Template",
        "metadata": {
            "namespace": "default",
            "name": args.name,
            "title": args.title,
            "description": args.description,
            "tags": ["ansible", "self-service"],
        },
        "spec": {
            "type": "service",
            "parameters": [form_page, auth_page],
            "steps": [
                {
                    "id": "launch-job",
                    "name": args.job_template,
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
                        "title": "Request submitted",
                        "content": (
                            "**Job ID:** ${{ steps['launch-job'].output.data.id }}\n"
                            "**Status:** ${{ steps['launch-job'].output.data.status }}\n"
                            f"**Selection:** ${{{{ parameters.{p_var} }}}} / "
                            f"${{{{ parameters.{c_var} }}}}\n"
                        ),
                    }
                ]
            },
        },
    }

    header = (
        "# ── GENERATED FILE — DO NOT EDIT ─────────────────────────────\n"
        f"# Generated by generate_cascade.py from {args.data}\n"
        f"# ({len(grouped)} {p_var} options, "
        f"{sum(len(v) for v in grouped.values())} {c_var} options)\n"
        "# ─────────────────────────────────────────────────────────────\n"
    )
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(header + yaml.safe_dump(template, sort_keys=False, width=100, allow_unicode=True))

    print(f"wrote {args.out}: {len(grouped)} {args.parent} -> "
          f"{sum(len(v) for v in grouped.values())} {args.child} options")


if __name__ == "__main__":
    main()