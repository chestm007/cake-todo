from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, ListItem, ListView, Select, SelectionList, Static, TextArea

from .config import Config
from .org import OrgStore, Task


class TaskForm(ModalScreen[Task | None]):
    BINDINGS = [("ctrl+enter", "submit", "Submit")]

    def __init__(self, task: Task | None = None, tags: list[str] | None = None):
        super().__init__()
        self._todo = task
        self.tags = tags or []

    def compose(self) -> ComposeResult:
        t = self._todo
        yield Vertical(
            Label("Edit task" if t else "New task"),
            Input(t.title if t else "", placeholder="Title", id="title"),
            SelectionList(*[(tag, tag, bool(t and tag in t.tags)) for tag in self.tags], id="tag-picker"),
            Input("", placeholder="New tags (comma separated)", id="tags"),
            Input(t.due.isoformat() if t and t.due else "", placeholder="Due date (YYYY-MM-DD)", id="due"),
            Select(
                [("No due date", ""), ("Today", date.today().isoformat()),
                 ("Tomorrow", (date.today() + timedelta(days=1)).isoformat()),
                 ("Next week", (date.today() + timedelta(days=7)).isoformat())],
                value=t.due.isoformat() if t and t.due else "", id="due-picker",
            ),
            Input(t.assigned_by if t else "", placeholder="Assigned by (for example @krut)", id="assigned"),
            Checkbox("Urgent", value=t.urgent if t else False, id="urgent"),
            TextArea(t.body_as_text() if t else "", id="body"),
            Horizontal(Button("Save", variant="primary", id="save"), Button("Cancel", id="cancel")),
            id="dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        self.submit()

    def action_submit(self) -> None:
        self.submit()

    def submit(self) -> None:
        title = self.query_one("#title", Input).value.strip()
        if not title:
            self.notify("A title is required", severity="error")
            return
        due_text = self.query_one("#due", Input).value.strip() or str(self.query_one("#due-picker", Select).value or "")
        try:
            due = date.fromisoformat(due_text) if due_text else None
        except ValueError:
            self.notify("Due date must be YYYY-MM-DD", severity="error")
            return
        picked = list(self.query_one("#tag-picker", SelectionList).selected)
        new_tags = [x.strip() for x in self.query_one("#tags", Input).value.split(",") if x.strip()]
        tags = list(dict.fromkeys(picked + new_tags))
        task = self._todo or Task(Path(), 0, 0, 1, title)
        task.title = title
        task.tags = tags
        task.due = due
        task.assigned_by = self.query_one("#assigned", Input).value.strip()
        task.urgent = self.query_one("#urgent", Checkbox).value
        task.body = self.query_one("#body", TextArea).text.splitlines()
        self.dismiss(task)


class HierarchyForm(ModalScreen[list[str] | None]):
    BINDINGS = [("ctrl+enter", "submit", "Submit")]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Classification hierarchy (highest first)"),
            Input("", placeholder="Customer, Personal, Learning", id="hierarchy"),
            Horizontal(Button("Save", variant="primary", id="save"), Button("Cancel", id="cancel")),
            id="dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#hierarchy", Input).value = ", ".join(self.app.config.hierarchy)  # type: ignore[attr-defined]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        else:
            self.submit()

    def action_submit(self) -> None:
        self.submit()

    def submit(self) -> None:
        values = [x.strip() for x in self.query_one("#hierarchy", Input).value.split(",") if x.strip()]
        self.dismiss(values)


class ProgressForm(ModalScreen[str | None]):
    BINDINGS = [("ctrl+enter", "submit", "Submit")]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Add progress note"),
            TextArea(id="progress-note"),
            Horizontal(Button("Add", variant="primary", id="add"), Button("Cancel", id="cancel")),
            id="dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        self.submit()

    def action_submit(self) -> None:
        self.submit()

    def submit(self) -> None:
        note = self.query_one("#progress-note", TextArea).text.strip()
        if not note:
            self.notify("A progress note is required", severity="error")
            return
        self.dismiss(note)


class DeleteConfirmation(ModalScreen[bool]):
    def __init__(self, title: str):
        super().__init__()
        self.title_text = title

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"Delete task?\n\n{self.title_text}"),
            Horizontal(Button("Delete", variant="error", id="delete"), Button("Cancel", id="cancel")),
            id="dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "delete")


class TodoApp(App):
    TITLE = "Org Todo"
    CSS = """
    Screen { background: $surface; }
    #dialog { width: 70; height: auto; padding: 1 2; border: round $accent; background: $surface; }
    #dialog Input, #dialog Checkbox { margin: 1 0; }
    #dialog Button { margin: 1 1; }
    #empty { padding: 2; color: $text-muted; }
    #content { height: 1fr; }
    #tasks { width: 1fr; }
    #details { width: 1fr; height: 100%; padding: 1 2; border: round $accent; }
    #notes-scroll { height: 1fr; margin-top: 1; padding: 1 2; background: $surface-darken-1; }
    """
    BINDINGS = [("j", "move_down", "Down"), ("k", "move_up", "Up"), ("a", "add", "Add"), ("e", "edit", "Edit"), ("p", "progress", "Progress"), ("x", "toggle_done", "Complete"), ("n", "next_task", "Next task"), ("d", "delete", "Delete"), ("h", "hierarchy", "Hierarchy"), ("r", "reload", "Reload"), ("q", "quit", "Quit")]

    def __init__(self, org_dir: Path):
        super().__init__()
        self.store = OrgStore(org_dir)
        self.config = Config()
        self.tasks: list[Task] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="summary")
        yield Horizontal(
            ListView(id="tasks"),
            Vertical(
                Static("Select a task to view its details.", id="details-meta"),
                ScrollableContainer(Static("", id="details-notes"), id="notes-scroll"),
                id="details",
            ),
            id="content",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.reload()

    def reload(self) -> None:
        self.tasks = sorted(self.store.load(), key=lambda t: t.sort_key(self.config.hierarchy))
        view = self.query_one("#tasks", ListView)
        view.clear()
        if not self.tasks:
            view.append(ListItem(Label("No tasks. Press 'a' to add one."), id="empty"))
        for task in self.tasks:
            due = task.due.isoformat() if task.due else "no due date"
            state = "✓" if task.done else "○"
            tag = task.most_important_tag(self.config.hierarchy)
            line = Text(f"{state} [")
            line.append(tag, style="bold" if task.urgent else "")
            line.append(f"] {task.title}  {due}")
            view.append(ListItem(Label(line)))
        self.query_one("#summary", Static).update(f"{len(self.tasks)} tasks | hierarchy: {', '.join(self.config.hierarchy) or '(none)'}")
        self.update_details()

    def selected(self) -> Task | None:
        view = self.query_one("#tasks", ListView)
        if view.index is None or not self.tasks or view.index >= len(self.tasks):
            return None
        return self.tasks[view.index]

    def update_details(self) -> None:
        task = self.selected()
        metadata = self.query_one("#details-meta", Static)
        notes_view = self.query_one("#details-notes", Static)
        if task is None:
            metadata.update("Select a task to view its details.")
            notes_view.update("")
            return
        status = "DONE" if task.done else "TODO"
        tags = ", ".join(task.tags) or "(none)"
        due = task.due.isoformat() if task.due else "(none)"
        assigned = task.assigned_by or "(none)"
        notes = task.body_as_text() or "(none)"
        progress = task.progress_as_text()
        metadata.update(
            f"[b]{task.title}[/b]\n"
            f"Status: {status}    Urgent: {'yes' if task.urgent else 'no'}\n"
            f"Tags: {tags}\nDue: {due}    Assigned by: {assigned}\n"
            f"File: {task.path}\nNotes:"
        )
        notes_view.update(f"{notes}\n\nProgress:\n\n{progress}" if progress else notes)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self.update_details()

    def action_move_down(self) -> None:
        view = self.query_one("#tasks", ListView)
        if view.index is not None:
            view.index = min(view.index + 1, max(0, len(self.tasks) - 1))

    def action_move_up(self) -> None:
        view = self.query_one("#tasks", ListView)
        if view.index is not None:
            view.index = max(0, view.index - 1)

    def action_add(self) -> None:
        self.push_screen(TaskForm(tags=self.all_tags()), self.created)

    def created(self, task: Task | None) -> None:
        if task:
            task.path = self.store.default_file
            self.store.add(task)
            self.reload()

    def action_edit(self) -> None:
        task = self.selected()
        if task:
            self.push_screen(TaskForm(task, self.all_tags()), self.edited)

    def action_progress(self) -> None:
        task = self.selected()
        if task:
            self.push_screen(ProgressForm(), lambda note: self.add_progress(task, note))

    def add_progress(self, task: Task, note: str | None) -> None:
        if note:
            task.progress.append((date.today().isoformat(), note))
            self.store.save(task)
            self.reload()

    def edited(self, task: Task | None) -> None:
        if task:
            self.store.save(task)
            self.reload()

    def action_toggle_done(self) -> None:
        task = self.selected()
        if task:
            task.done = not task.done
            self.store.save(task)
            self.reload()

    def action_next_task(self) -> None:
        """Select the first actionable task using the strict ordering rules."""
        for index, task in enumerate(self.tasks):
            if not task.done:
                self.query_one("#tasks", ListView).index = index
                return
        self.notify("There are no open tasks")

    def action_delete(self) -> None:
        task = self.selected()
        if task:
            self.push_screen(DeleteConfirmation(task.title), lambda confirmed: self.delete_confirmed(task, confirmed))

    def delete_confirmed(self, task: Task, confirmed: bool) -> None:
        if confirmed:
            self.store.delete(task)
            self.reload()

    def action_hierarchy(self) -> None:
        self.push_screen(HierarchyForm(), self.hierarchy_changed)

    def hierarchy_changed(self, hierarchy: list[str] | None) -> None:
        if hierarchy is not None:
            self.config.hierarchy = hierarchy
            self.config.save()
            self.reload()

    def action_reload(self) -> None:
        self.reload()

    def all_tags(self) -> list[str]:
        return sorted({tag for task in self.tasks for tag in task.tags})


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Org-file todos")
    parser.add_argument("--org-dir", type=Path, default=Path.home() / "orgfiles")
    args = parser.parse_args()
    TodoApp(args.org_dir).run()


if __name__ == "__main__":
    main()
