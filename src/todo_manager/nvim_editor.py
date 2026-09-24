from __future__ import annotations

import threading
from typing import Any

import pynvim
from rich.text import Text
from rich.style import Style
from textual import events
from textual.widget import Widget


class NvimEditor(Widget):
    """A small Textual widget backed by a headless Neovim UI.

    Neovim owns editing, including normal, insert, and visual modes. The
    widget only translates keys and renders the line-grid UI notifications.
    """

    can_focus = True

    def __init__(self, text: str = "", *, syntax: str = "org", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._initial_text = text
        self._syntax = syntax
        self._nvim: Any = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        initial_lines = text.splitlines() or [""]
        self._grid: list[list[str]] = [list(line) for line in initial_lines]
        self._styles: list[list[int]] = [[0] * len(line) for line in initial_lines]
        self._hl_attrs: dict[int, dict[str, Any]] = {}
        self._cursor = (0, 0)
        self._ready = threading.Event()

    def on_mount(self) -> None:
        self._thread = threading.Thread(target=self._run_nvim, daemon=True)
        self._thread.start()

    def on_unmount(self) -> None:
        nvim = self._nvim
        if nvim is not None:
            try:
                nvim.async_call(lambda: nvim.command("qa!"))
            except Exception:
                pass

    def on_resize(self, event: events.Resize) -> None:
        nvim = self._nvim
        if nvim is not None:
            try:
                nvim.async_call(
                    lambda: nvim.ui_try_resize(max(1, event.size.width), max(1, event.size.height))
                )
            except Exception:
                pass

    def _run_nvim(self) -> None:
        from pynvim.msgpack_rpc.event_loop.asyncio import AsyncioEventLoop

        # pynvim's worker event loop tries to install process signal handlers,
        # which Python only permits from the main thread. Neovim is owned by
        # this widget's worker, so signal handling belongs to the parent app.
        AsyncioEventLoop._setup_signals = lambda _self, _signals: None
        AsyncioEventLoop._teardown_signals = lambda _self: None

        nvim = pynvim.attach(
            "child",
            argv=["nvim", "--clean", "--headless", "--embed"],
        )
        self._nvim = nvim
        nvim.ui_attach(max(1, self.size.width), max(1, self.size.height), rgb=True, ext_linegrid=True)
        lines = self._initial_text.splitlines()
        nvim.current.buffer[:] = lines or [""]
        nvim.command(f"set filetype={self._syntax}")
        self._ready.set()
        try:
            nvim.run_loop(lambda *_: None, self._on_notification)
        except EOFError:
            # Expected when the owning modal is dismissed.
            pass
        finally:
            try:
                nvim.close()
            except Exception:
                pass

    def _on_notification(self, method: str, args: list[Any]) -> None:
        if method != "redraw":
            return
        for event in args:
            if not event:
                continue
            name, *payload = event
            handler = getattr(self, f"_redraw_{name}", None)
            if handler is not None:
                for item in payload:
                    handler(*item if isinstance(item, list) else (item,))
        try:
            self.app.call_from_thread(self.refresh)
        except RuntimeError:
            pass

    def _redraw_grid_resize(self, _grid: int, width: int, height: int) -> None:
        with self._lock:
            self._grid = [[" "] * width for _ in range(height)]
            self._styles = [[0] * width for _ in range(height)]

    def _redraw_grid_clear(self, _grid: int) -> None:
        with self._lock:
            for row, styles in zip(self._grid, self._styles):
                row[:] = [" "] * len(row)
                styles[:] = [0] * len(styles)

    def _redraw_hl_attr_define(
        self,
        hl_id: int,
        rgb_attrs: dict[str, Any],
        _cterm_attrs: dict[str, Any],
        _info: list[Any],
    ) -> None:
        self._hl_attrs[hl_id] = rgb_attrs

    def _redraw_grid_line(
        self,
        _grid: int,
        row: int,
        col_start: int,
        cells: list[list[Any]],
        _wrap: bool,
        repeat: int = 1,
    ) -> None:
        with self._lock:
            for _ in range(repeat):
                if row >= len(self._grid):
                    return
                column = col_start
                current_hl_id = self._styles[row][column] if column < len(self._styles[row]) else 0
                for cell in cells:
                    value = cell[0]
                    if len(cell) > 1:
                        current_hl_id = cell[1]
                    cell_repeat = cell[2] if len(cell) > 2 else 1
                    for _ in range(cell_repeat):
                        for character in value:
                            if column < len(self._grid[row]):
                                self._grid[row][column] = character
                                self._styles[row][column] = current_hl_id
                            column += 1
                row += 1

    def _redraw_grid_cursor_goto(self, _grid: int, row: int, column: int) -> None:
        self._cursor = (row, column)

    def _redraw_grid_scroll(
        self,
        _grid: int,
        top: int,
        bottom: int,
        left: int,
        right: int,
        rows: int,
        columns: int,
    ) -> None:
        """Apply Neovim's viewport scroll operation to the rendered grid."""
        with self._lock:
            old_grid = [row[:] for row in self._grid]
            old_styles = [row[:] for row in self._styles]
            for row in range(top, min(bottom, len(self._grid))):
                for column in range(left, min(right, len(self._grid[row]))):
                    source_row = row + rows
                    source_column = column + columns
                    if (
                        top <= source_row < bottom
                        and left <= source_column < right
                        and source_row < len(old_grid)
                        and source_column < len(old_grid[source_row])
                    ):
                        self._grid[row][column] = old_grid[source_row][source_column]
                        self._styles[row][column] = old_styles[source_row][source_column]
                    else:
                        self._grid[row][column] = " "
                        self._styles[row][column] = 0

    def render(self) -> Text:
        with self._lock:
            lines = ["".join(row) for row in self._grid]
            styles = [row[:] for row in self._styles]
            cursor_row, cursor_column = self._cursor
        if 0 <= cursor_row < len(lines):
            if cursor_column >= len(lines[cursor_row]):
                lines[cursor_row] += " " * (cursor_column - len(lines[cursor_row]) + 1)
        rendered = Text("\n".join(lines))
        offset = 0
        for row, line in enumerate(lines):
            for column, hl_id in enumerate(styles[row]):
                style = self._rich_style(hl_id)
                if style is not None:
                    rendered.stylize(style, offset + column, offset + column + 1)
            offset += len(line) + 1
        if 0 <= cursor_row < len(lines) and cursor_column < len(lines[cursor_row]):
            start = sum(len(line) + 1 for line in lines[:cursor_row]) + cursor_column
            rendered.stylize("reverse", start, start + 1)
        return rendered

    def _rich_style(self, hl_id: int) -> Style | None:
        attrs = self._hl_attrs.get(hl_id)
        if not attrs:
            return None
        kwargs: dict[str, Any] = {}
        for source, target in (("foreground", "color"), ("background", "bgcolor")):
            value = attrs.get(source)
            if isinstance(value, int):
                kwargs[target] = f"#{value:06x}"
        for name in ("bold", "italic", "underline", "strikethrough", "reverse"):
            if attrs.get(name):
                kwargs[name] = True
        return Style(**kwargs) if kwargs else None

    async def on_key(self, event: events.Key) -> None:
        # Let the modal form's Ctrl+Enter binding submit the form.
        if event.key in {"ctrl+enter", "ctrl+c"}:
            return
        nvim = self._nvim
        if nvim is None or not self._ready.wait(timeout=0.1):
            return
        key = self._to_nvim_key(event)
        if key is None:
            return
        event.stop()
        event.prevent_default()
        try:
            nvim.async_call(lambda: nvim.input(key))
        except Exception:
            pass

    async def on_paste(self, event: events.Paste) -> None:
        """Forward terminal clipboard text through Neovim's paste API."""
        nvim = self._nvim
        if nvim is None or not self._ready.wait(timeout=0.1):
            return
        event.stop()
        try:
            nvim.async_call(
                lambda: nvim.request("nvim_paste", event.text, False, -1)
            )
        except Exception:
            pass

    @staticmethod
    def _to_nvim_key(event: events.Key) -> str | None:
        special = {
            "escape": "<Esc>",
            "enter": "<CR>",
            "backspace": "<BS>",
            "delete": "<Del>",
            "tab": "<Tab>",
            "up": "<Up>",
            "down": "<Down>",
            "left": "<Left>",
            "right": "<Right>",
            "home": "<Home>",
            "end": "<End>",
            "pageup": "<PageUp>",
            "pagedown": "<PageDown>",
        }
        if event.key in special:
            return special[event.key]
        if event.key.startswith("ctrl+") and len(event.key) == 6:
            return f"<C-{event.key[-1].upper()}>"
        if event.is_printable:
            return event.character
        return None

    @property
    def text(self) -> str:
        nvim = self._nvim
        if nvim is None:
            return self._initial_text
        result: list[str] = []
        complete = threading.Event()

        def read_buffer() -> None:
            try:
                result.extend(nvim.current.buffer[:])
            finally:
                complete.set()

        try:
            nvim.async_call(read_buffer)
            complete.wait(timeout=1)
            return "\n".join(result)
        except Exception:
            return self._initial_text
