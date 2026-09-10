from __future__ import annotations

import scripts.panel as panel_entry


def test_windows_panel_open_uses_shell_execute(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(panel_entry.sys, "platform", "win32")
    monkeypatch.setattr(panel_entry.os, "startfile", opened.append)
    monkeypatch.setattr(
        panel_entry.webbrowser,
        "open",
        lambda _url: (_ for _ in ()).throw(AssertionError("fallback used")),
    )

    panel_entry.open_panel_in_browser(8765)

    assert opened == ["http://127.0.0.1:8765"]


def test_runtime_self_test_loads_wasmtime() -> None:
    assert panel_entry.runtime_self_test() is True
