from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

HEADING = re.compile(r"^(?P<stars>\*+)\s+(?P<state>TODO|DONE)\s+(?P<title>.*?)(?:\s+(?P<tags>:[^:]+(?::[^:]+)*:))?\s*$")
SCHEDULED = re.compile(r"^\s*(?:SCHEDULED|DEADLINE):\s*<(?P<date>\d{4}-\d{2}-\d{2})(?:[^>]*)>\s*$")
PROPERTY = re.compile(r"^\s*:(?P<key>[A-Z0-9_-]+):\s*(?P<value>.*)$")


@dataclass
class Task:
    path: Path
    line: int
    end_line: int
    level: int
    title: str
    tags: list[str] = field(default_factory=list)
    due: date | None = None
    assigned_by: str = ""
    urgent: bool = False
    done: bool = False
    body: list[str] = field(default_factory=list)
    progress: list[tuple[str, str]] = field(default_factory=list)

    @property
    def classification(self) -> str:
        return self.tags[0] if self.tags else "Unclassified"

    def most_important_tag(self, hierarchy: list[str]) -> str:
        if not self.tags:
            return "Unclassified"
        rank = {name.casefold(): i for i, name in enumerate(hierarchy)}
        return min(self.tags, key=lambda tag: rank.get(tag.casefold(), len(rank)))

    def sort_key(self, hierarchy: list[str]) -> tuple:
        rank = {name.casefold(): i for i, name in enumerate(hierarchy)}
        classification_rank = min((rank.get(tag.casefold(), len(rank)) for tag in self.tags), default=len(rank))
        due = self.due or date.max
        # Completed work is kept in the list, but always follows actionable work.
        if self.done:
            return (2, due, classification_rank)
        return (0, due, 0) if self.urgent else (1, classification_rank, due)

    def body_as_text(self) -> str:
        """Return the task body without changing Org formatting."""
        return "\n".join(self.body)

    def progress_as_text(self) -> str:
        entries = []
        for when, note in self.progress:
            continuation_indent = " " * (len(when) + 2)
            entries.append(f"{when}  {note.replace(chr(10), chr(10) + continuation_indent)}")
        return "\n\n".join(entries)

    def to_org(self) -> str:
        tags = f" {' '.join(':' + t + ':' for t in self.tags)}" if self.tags else ""
        state = "DONE" if self.done else "TODO"
        lines = [f"{'*' * self.level} {state} {self.title}{tags}"]
        if self.due:
            lines.append(f"  SCHEDULED: <{self.due.isoformat()}>")
        if self.assigned_by or self.urgent:
            lines += ["  :PROPERTIES:"]
            if self.assigned_by:
                lines.append(f"  :ASSIGNED-BY: {self.assigned_by}")
            if self.urgent:
                lines.append("  :URGENT: t")
            lines.append("  :END:")
        if self.progress:
            lines.append("  :LOGBOOK:")
            for when, note in self.progress:
                note_lines = note.splitlines() or [""]
                lines.append(f"  - [{when}] {note_lines[0]}")
                lines.extend(f"    {line}" for line in note_lines[1:])
            lines.append("  :END:")
        lines.extend(self.body)
        return "\n".join(lines) + "\n"


class OrgStore:
    def __init__(self, directory: Path):
        self.directory = directory.expanduser()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.default_file = self.directory / "tasks.org"

    def load(self) -> list[Task]:
        tasks: list[Task] = []
        for path in sorted(self.directory.glob("*.org")):
            lines = path.read_text(encoding="utf-8").splitlines()
            matches = [(i, m) for i, line in enumerate(lines) if (m := HEADING.match(line))]
            for index, (start, match) in enumerate(matches):
                end = matches[index + 1][0] if index + 1 < len(matches) else len(lines)
                if len(match.group("stars")) > 1:
                    continue
                task = self._parse_task(path, lines, start, end, match)
                tasks.append(task)
        return tasks

    def _parse_task(self, path: Path, lines: list[str], start: int, end: int, match: re.Match) -> Task:
        tags = [x for x in (match.group("tags") or "").strip(":").split(":") if x]
        due = None
        assigned = ""
        urgent = False
        body: list[str] = []
        progress: list[tuple[str, str]] = []
        progress_note: tuple[str, list[str]] | None = None
        in_props = False
        in_logbook = False

        def flush_progress_note() -> None:
            nonlocal progress_note
            if progress_note is not None:
                when, note_lines = progress_note
                progress.append((when, "\n".join(note_lines)))
                progress_note = None

        for line in lines[start + 1:end]:
            if line.strip() == ":LOGBOOK:":
                in_logbook = True
                continue
            if in_logbook:
                if line.strip() == ":END:":
                    flush_progress_note()
                    in_logbook = False
                else:
                    entry = re.match(r"^\s*-\s*\[(?P<date>\d{4}-\d{2}-\d{2})\]\s+(?P<note>.+)$", line)
                    if entry:
                        flush_progress_note()
                        progress_note = (entry.group("date"), [entry.group("note").strip()])
                    elif progress_note is not None:
                        progress_note[1].append(line.strip())
                continue
            scheduled = SCHEDULED.match(line)
            prop = PROPERTY.match(line)
            if scheduled:
                due = date.fromisoformat(scheduled.group("date"))
            elif prop and prop.group("key") == "PROPERTIES":
                in_props = True
            elif prop and prop.group("key") == "END":
                in_props = False
            elif in_props and prop:
                if prop.group("key") == "ASSIGNED-BY":
                    assigned = prop.group("value").strip()
                elif prop.group("key") == "URGENT":
                    urgent = prop.group("value").strip().lower() in {"t", "true", "yes", "1"}
            else:
                body.append(line)
        return Task(path, start, end, len(match.group("stars")), match.group("title").strip(), tags, due, assigned, urgent, match.group("state") == "DONE", body, progress)

    def save(self, task: Task) -> None:
        lines = task.path.read_text(encoding="utf-8").splitlines(keepends=True) if task.path.exists() else []
        replacement = task.to_org()
        # line/end_line are from the last load; save immediately after each edit.
        lines[task.line:task.end_line] = [replacement]
        task.path.write_text("".join(lines), encoding="utf-8")

    def add(self, task: Task) -> None:
        self.default_file.parent.mkdir(parents=True, exist_ok=True)
        with self.default_file.open("a", encoding="utf-8") as handle:
            if self.default_file.stat().st_size:
                handle.write("\n")
            handle.write(task.to_org())

    def delete(self, task: Task) -> None:
        lines = task.path.read_text(encoding="utf-8").splitlines(keepends=True)
        del lines[task.line:task.end_line]
        task.path.write_text("".join(lines), encoding="utf-8")
