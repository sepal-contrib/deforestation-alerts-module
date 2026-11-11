"""The reviewer's SEPAL user name, looked up once per session (gui.scripts.reviewer)."""

import threading
from types import SimpleNamespace

from gui.scripts import reviewer


class _Client:
    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.urls = payload, error, []

    def get(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return SimpleNamespace(json=lambda: self.payload)


def test_session_name_wins():
    client = _Client({"username": "api"})
    assert reviewer.lookup_name("ana", client, environ={"SEPAL": "true"}) == "ana"
    assert client.urls == []


def test_api_username():
    client = _Client({"username": "jserafini"})
    assert reviewer.lookup_name(None, client, environ={"SEPAL": "true"}) == "jserafini"
    # SepalClient's base_url is the bare https://<SEPAL_HOST>; the path carries /api
    assert client.urls == ["/api/user/current"]
    assert client.urls[0].endswith("/user/current")


def test_api_error_gives_empty():
    client = _Client(error=RuntimeError("401"))
    assert reviewer.lookup_name("", client, environ={"SEPAL": "true"}) == ""


def test_local_dev_env_then_getpass(monkeypatch):
    monkeypatch.setattr(reviewer.getpass, "getuser", lambda: "osuser")
    assert reviewer.lookup_name(None, None, environ={"LOCAL_SEPAL_USER": "dev"}) == "dev"
    assert reviewer.lookup_name(None, None, environ={}) == "osuser"


def test_never_the_sandbox_user_on_sepal(monkeypatch):
    monkeypatch.setattr(reviewer.getpass, "getuser", lambda: "sepal-user")
    assert reviewer.lookup_name(None, None, environ={"SEPAL": "true"}) == ""


def test_start_lookup_sets_app_state(monkeypatch):
    monkeypatch.setattr(
        reviewer, "get_current_session_info", lambda: SimpleNamespace(username=None)
    )
    monkeypatch.setattr(
        reviewer, "get_current_sepal_client", lambda: _Client({"username": "ana"})
    )
    started = []
    real_thread = threading.Thread

    def _thread(*args, **kwargs):
        t = real_thread(*args, **kwargs)
        started.append(t)
        return t

    monkeypatch.setattr(reviewer.threading, "Thread", _thread)
    app_state = SimpleNamespace(reviewer="")
    reviewer.start_lookup(app_state)
    assert len(started) == 1 and started[0].daemon
    started[0].join(5)
    assert app_state.reviewer == "ana"


def test_start_lookup_survives_reader_errors(monkeypatch):
    def _boom():
        raise RuntimeError("no session")

    monkeypatch.setattr(reviewer, "get_current_session_info", _boom)
    monkeypatch.setattr(reviewer, "get_current_sepal_client", _boom)
    monkeypatch.setattr(reviewer.getpass, "getuser", lambda: "sepal-user")
    monkeypatch.setenv("SEPAL", "true")
    app_state = SimpleNamespace(reviewer="")
    reviewer.start_lookup(app_state)  # must not raise in the render effect
