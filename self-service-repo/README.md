# Self-service portal templates

Templates for the Ansible Automation Platform self-service portal are **generated**
from small config files. Nobody hand-writes Backstage YAML.

```
.
├── catalog-info.yaml       # GENERATED — Location entity; register this ONCE in the portal
├── configs/                # ← you edit these: one config per template
│   ├── server-config-request.yaml
│   └── service-restart-request.yaml
├── data/                   # ← you edit these: option lists for cascading fields
│   ├── host_configs.json
│   └── env_host_service.json
├── templates/              # GENERATED — never edit by hand
│   ├── server-config-request.yaml
│   └── service-restart-request.yaml
└── generator/
    ├── build_template.py   # builds one template from one config
    ├── build_all.py        # builds every config + refreshes catalog-info.yaml
    └── requirements.txt
```

## Setup (once)

```bash
pip3 install -r generator/requirements.txt
```

## Everyday tasks

| I want to…                        | Do this                                                        |
|-----------------------------------|----------------------------------------------------------------|
| Update the options in a dropdown  | Edit the file in `data/`, then build                            |
| Change a form field / job template| Edit the file in `configs/`, then build                         |
| Add a new template                | Copy a config in `configs/`, edit it, then build                |
| Build everything                  | `python3 generator/build_all.py`                                |
| Build one template                | `python3 generator/build_all.py server-config`                  |
| Preview without touching the repo | `python3 generator/build_template.py --config configs/x.yaml --out /tmp/x.yaml` |

Then commit and push. The portal picks up changes on its next catalog refresh.

## Portal registration

Register **`catalog-info.yaml`** once, using its raw URL. It is a `Location`
entity listing every generated template, so templates added later appear
automatically — no further registration.

## Rules

- **Never edit anything in `templates/`** — it is overwritten on every build.
- Keep `template.name` and `output_file` stable in a config; the portal tracks
  a template by that name, and the registered Location points at that path.
- Data files are flat lists of records; the builder groups them into the
  cascading dropdowns. Any number of levels is supported.
- `var:` on a field renames only the AAP extra variable, not the form field.

## Config reference

See the comments in `configs/server-config-request.yaml` (two-level cascade,
text and number fields) and `configs/service-restart-request.yaml`
(three-level cascade, boolean field).

Field types: `cascade`, `select`, `text`, `textarea`, `number`, `integer`,
`boolean`, `password`.

> Note: `password` masks typing only. The value is still visible on the review
> page and in the AAP job's extra vars — use AAP credentials for real secrets.
