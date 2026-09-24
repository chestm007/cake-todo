from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ClickUpError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClickUpCandidate:
    key: str
    workspace: str
    list_name: str
    task_id: str
    title: str
    body: str
    url: str
    due: str | None
    urgent: bool

    @property
    def label(self) -> str:
        return f"[ClickUp] {self.workspace}/{self.list_name}  {self.title}"


class ClickUpImportRegister:
    def __init__(self, path: Path | None = None):
        self.path = path or Path.home() / ".config" / "todo-manager" / "clickup-imports.json"
        self.entries: dict[str, dict[str, str]] = {}
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = dict(data) if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            self.entries = {}

    def contains(self, key: str) -> bool:
        return key in self.entries

    def task_id_for_url(self, url: str) -> str | None:
        for entry in self.entries.values():
            if entry.get("url") == url:
                return entry.get("task_id")
        return None

    def mark(self, candidate: ClickUpCandidate, state: str) -> None:
        self.entries[candidate.key] = {
            "state": state,
            "task_id": candidate.task_id,
            "workspace": candidate.workspace,
            "list": candidate.list_name,
            "url": candidate.url,
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class ClickUpClient:
    API = "https://api.clickup.com/api/v2"

    def __init__(self, token_path: Path | None = None):
        self.token_path = token_path or Path.home() / ".clickup_token"
        try:
            self.token = self.token_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as error:
            raise ClickUpError(f"ClickUp token not found at {self.token_path}") from error
        if not self.token:
            raise ClickUpError(f"ClickUp token at {self.token_path} is empty")

    def _get(self, path: str, params: dict[str, str | int | bool] | None = None) -> dict:
        url = f"{self.API}{path}"
        if params:
            url += "?" + urlencode(params)
        request = Request(url, headers={"Authorization": self.token, "Content-Type": "application/json", "User-Agent": "todo-manager"})
        try:
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            if error.code == 429:
                retry_after = error.headers.get("Retry-After", "later")
                raise ClickUpError(f"ClickUp rate limit reached; retry after {retry_after} seconds") from error
            raise ClickUpError(f"ClickUp API returned {error.code}: {detail}") from error
        except URLError as error:
            raise ClickUpError(f"Could not reach ClickUp: {error.reason}") from error

    def _put(self, path: str, payload: dict) -> dict:
        request = Request(
            f"{self.API}{path}",
            data=json.dumps(payload).encode("utf-8"),
            method="PUT",
            headers={
                "Authorization": self.token,
                "Content-Type": "application/json",
                "User-Agent": "todo-manager",
            },
        )
        try:
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            if error.code == 429:
                retry_after = error.headers.get("Retry-After", "later")
                raise ClickUpError(f"ClickUp rate limit reached; retry after {retry_after} seconds") from error
            raise ClickUpError(f"ClickUp API returned {error.code}: {detail}") from error
        except URLError as error:
            raise ClickUpError(f"Could not reach ClickUp: {error.reason}") from error

    def complete_task(self, task_id: str) -> None:
        task = self._get(f"/task/{task_id}")
        list_id = (task.get("list") or {}).get("id")
        if not list_id:
            raise ClickUpError("ClickUp task has no list, so its closed status could not be found")
        statuses = self._get(f"/list/{list_id}/status").get("statuses", [])
        closed = next((status for status in statuses if status.get("type") == "closed"), None)
        if closed is None:
            closed = next(
                (status for status in statuses if status.get("status", "").casefold() in {"done", "closed", "complete", "completed"}),
                None,
            )
        if closed is None:
            raise ClickUpError("No closed status is configured for the ClickUp task list")
        self._put(f"/task/{task_id}", {"status": closed["status"]})

    def candidates(self, register: ClickUpImportRegister) -> list[ClickUpCandidate]:
        candidates: dict[str, ClickUpCandidate] = {}
        user_id = self._get("/user")["user"]["id"]
        for team in self._get("/team").get("teams", []):
            workspace = team.get("name", team["id"])
            tasks = self._tasks(team["id"], user_id)
            tasks_by_id = {task["id"]: task for task in tasks}
            for task in tasks:
                parent_id = task.get("parent")
                while parent_id and parent_id not in tasks_by_id:
                    parent = self._get(f"/task/{parent_id}")
                    tasks_by_id[parent_id] = parent
                    parent_id = parent.get("parent")
            for task in tasks:
                task_list = task.get("list") or {}
                candidate = self._candidate(
                    task,
                    workspace,
                    task_list.get("name", "Unknown list"),
                    self._title_with_parent(task, tasks_by_id),
                )
                if not register.contains(candidate.key):
                    candidates[candidate.key] = candidate
        return sorted(candidates.values(), key=lambda item: (item.workspace.casefold(), item.list_name.casefold(), item.title.casefold()))

    def _tasks(self, team_id: str, user_id: int) -> list[dict]:
        tasks: list[dict] = []
        for page in range(100):
            result = self._get(
                f"/team/{team_id}/task",
                {
                    "archived": "false",
                    "include_closed": "false",
                    "subtasks": "true",
                    "assignees[]": user_id,
                    "page": page,
                },
            )
            page_tasks = result.get("tasks", [])
            tasks.extend(page_tasks)
            if result.get("last_page", True) or not page_tasks:
                break
        return tasks

    @staticmethod
    def _title_with_parent(task: dict, tasks_by_id: dict[str, dict]) -> str:
        titles = [task.get("name", "Untitled task")]
        parent_id = task.get("parent")
        seen = {task.get("id")}
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = tasks_by_id.get(parent_id)
            if parent is None:
                break
            titles.append(parent.get("name", "Untitled task"))
            parent_id = parent.get("parent")
        return " / ".join(reversed(titles))

    @staticmethod
    def _candidate(task: dict, workspace: str, list_name: str, title: str) -> ClickUpCandidate:
        priority = (task.get("priority") or {}).get("priority", "")
        due = task.get("due_date")
        due_date = datetime.fromtimestamp(int(due) / 1000, timezone.utc).date().isoformat() if due else None
        return ClickUpCandidate(
            key=f"{task['id']}",
            workspace=workspace,
            list_name=list_name,
            task_id=task["id"],
            title=title,
            body=task.get("description") or "",
            url=task.get("url", ""),
            due=due_date,
            urgent=priority.casefold() == "urgent",
        )
