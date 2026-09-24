# Org Todo

Small Textual todo manager backed by `~/orgfiles/*.org`.

Multiline fields use a headless Neovim instance, so Neovim must be installed
and available as `nvim` on `PATH`. This provides normal, insert, and visual
editing modes while preserving Org body text.

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

Org heading levels are represented as a task tree. Use `a` for a top-level
task and `Shift+A` to add a child under the selected task.

Press `g` to manually import open GitHub issues assigned to you and pull
requests authored by you, assigned to you, or requesting your review. GitHub
access uses the token stored in `~/.github_token`; imported and ignored
candidates are recorded in `~/.config/todo-manager/github-imports.json`.

Press `c` to manually import open ClickUp tasks assigned to you from all
accessible workspaces. ClickUp access uses the token stored in `~/.clickup_token`; imported and
ignored candidates are recorded in `~/.config/todo-manager/clickup-imports.json`.
Marking an imported ClickUp task complete locally also updates its configured
closed status in ClickUp.

Tasks are written as ordinary Org headings with `TODO`/`DONE`, heading tags,
`SCHEDULED`, `ASSIGNED-BY`, and `URGENT` properties.
