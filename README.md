# Org Todo

Small Textual todo manager backed by `~/orgfiles/*.org`.

## Run

```sh
python -m venv .venv
.venv/bin/pip install -e .
.venv/bin/todo
```

Use `--org-dir PATH` to select another Org directory.

The classification hierarchy is managed with `h` and stored in
`~/.config/todo-manager/config.json`. Put the highest-priority classification
first. `n` selects the next open task according to the urgent/due-date and
classification ordering rules.

Tasks are written as ordinary Org headings with `TODO`/`DONE`, heading tags,
`SCHEDULED`, `ASSIGNED-BY`, and `URGENT` properties.
