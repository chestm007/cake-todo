from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class GitHubError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubCandidate:
    key: str
    kind: str
    repository: str
    number: int
    title: str
    body: str
    url: str
    author: str

    @property
    def label(self) -> str:
        marker = "PR" if self.kind == "pr" else "Issue"
        return f"[{marker}] {self.repository}#{self.number}  {self.title}"


class GitHubImportRegister:
    def __init__(self, path: Path | None = None):
        self.path = path or Path.home() / ".config" / "todo-manager" / "github-imports.json"
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

    def mark(self, candidate: GitHubCandidate, state: str) -> None:
        self.entries[candidate.key] = {
            "state": state,
            "kind": candidate.kind,
            "repository": candidate.repository,
            "number": str(candidate.number),
            "url": candidate.url,
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class GitHubClient:
    API = "https://api.github.com"

    def __init__(self, token_path: Path | None = None):
        self.token_path = token_path or Path.home() / ".github_token"
        try:
            self.token = self.token_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as error:
            raise GitHubError(f"GitHub token not found at {self.token_path}") from error
        if not self.token:
            raise GitHubError(f"GitHub token at {self.token_path} is empty")

    def _get(self, path: str, params: dict[str, str | int] | None = None) -> dict:
        url = f"{self.API}{path}"
        if params:
            url += "?" + urlencode(params)
        request = Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "todo-manager",
            },
        )
        try:
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise GitHubError(f"GitHub API returned {error.code}: {detail}") from error
        except URLError as error:
            raise GitHubError(f"Could not reach GitHub: {error.reason}") from error

    def candidates(self, register: GitHubImportRegister) -> list[GitHubCandidate]:
        self._get("/user")
        queries = (
            "is:open is:issue assignee:@me",
            "is:open is:pr author:@me",
            "is:open is:pr assignee:@me",
            "is:open is:pr review-requested:@me",
        )
        found: dict[str, GitHubCandidate] = {}
        for query in queries:
            for page in range(1, 11):
                result = self._get("/search/issues", {"q": query, "per_page": 100, "page": page})
                items = result.get("items", [])
                for item in items:
                    repository = item.get("repository", {}).get("full_name", "")
                    if not repository:
                        repository_url = item.get("repository_url", "").rstrip("/")
                        repository = "/".join(repository_url.split("/")[-2:])
                    kind = "pr" if "pull_request" in item else "issue"
                    candidate = GitHubCandidate(
                        key=f"{repository}#{item['number']}:{kind}",
                        kind=kind,
                        repository=repository,
                        number=item["number"],
                        title=item["title"],
                        body=item.get("body") or "",
                        url=item["html_url"],
                        author=item.get("user", {}).get("login", ""),
                    )
                    if not register.contains(candidate.key):
                        found[candidate.key] = candidate
                if len(items) < 100:
                    break
        return sorted(found.values(), key=lambda item: (item.repository.casefold(), item.number))
