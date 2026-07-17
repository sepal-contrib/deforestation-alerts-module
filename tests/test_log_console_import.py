"""Import guard: the LogConsole widget must stay importable without a kernel."""


def test_log_console_imports():
    from gui.widget.log_console import LogConsole  # noqa: F401
