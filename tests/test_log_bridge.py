"""Harness tests for gui.scripts.log_bridge (ported from the spatial_risk scaffold)."""

import logging

import pytest

from gui.scripts.log_bridge import (
    MAX_RECORDS,
    ReactiveLogHandler,
    clear_log_records,
    install_log_console_handler,
    log_records,
)

APP_LOGGER = "SEPALUI.deforestation_alerts"


@pytest.fixture(autouse=True)
def _clean():
    clear_log_records()
    yield
    clear_log_records()
    # Detach any handler the test installed so state never leaks across tests.
    logger = logging.getLogger(APP_LOGGER)
    for h in list(logger.handlers):
        if isinstance(h, ReactiveLogHandler):
            logger.removeHandler(h)


def _test_logger():
    logger = logging.getLogger(f"{APP_LOGGER}.test")
    logger.setLevel(logging.INFO)
    return logger


def test_install_is_idempotent():
    h1 = install_log_console_handler()
    h2 = install_log_console_handler()
    assert h1 is h2
    root = logging.getLogger(APP_LOGGER)
    assert sum(isinstance(h, ReactiveLogHandler) for h in root.handlers) == 1


def test_emit_appends_record():
    install_log_console_handler()
    _test_logger().info("hello %s", "world")
    assert log_records.value, "emit did not reach log_records"
    last = log_records.value[-1]
    assert last["msg"] == "hello world"
    assert last["level"] == "INFO"
    assert last["name"].startswith(APP_LOGGER)


def test_child_module_loggers_are_captured():
    """Every module logger in the app lives under the root name — one handler
    must capture them all (the reason _LOGGER_NAME is the tree root)."""
    install_log_console_handler()
    child = logging.getLogger(f"{APP_LOGGER}.recipe")
    child.setLevel(logging.INFO)
    child.info("from the recipe tile")
    assert any(r["msg"] == "from the recipe tile" for r in log_records.value)


def test_buffer_is_capped_at_max_records():
    install_log_console_handler()
    logger = _test_logger()
    for i in range(MAX_RECORDS + 25):
        logger.info("m%d", i)
    assert len(log_records.value) == MAX_RECORDS
    assert log_records.value[-1]["msg"] == f"m{MAX_RECORDS + 24}"


def test_clear_log_records():
    install_log_console_handler()
    _test_logger().info("something")
    assert log_records.value
    clear_log_records()
    assert log_records.value == tuple()
