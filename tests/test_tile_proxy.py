from gui.scripts.tile_proxy import (
    borrow_localtileserver_prefix,
    loopback_bridge_needed,
    prefer_http_proxy,
)

SEPAL = "/api/sandbox/jupyter/proxy/{port}"


def test_borrow_copies_generic_proxy_prefix():
    env = {"LOCALTILESERVER_CLIENT_PREFIX": SEPAL}
    borrow_localtileserver_prefix(env)
    assert env["VECTORTILESERVER_CLIENT_PREFIX"] == SEPAL


def test_borrow_ignores_server_specific_prefix():
    env = {"LOCALTILESERVER_CLIENT_PREFIX": "localtileserver-proxy/{port}"}
    borrow_localtileserver_prefix(env)
    assert "VECTORTILESERVER_CLIENT_PREFIX" not in env


def test_borrow_keeps_explicit_vector_prefix():
    env = {"LOCALTILESERVER_CLIENT_PREFIX": SEPAL,
           "VECTORTILESERVER_CLIENT_PREFIX": "/custom/{port}"}
    borrow_localtileserver_prefix(env)
    assert env["VECTORTILESERVER_CLIENT_PREFIX"] == "/custom/{port}"


def test_prefer_http_proxy_disables_bridge_when_prefixed():
    env = {"LOCALTILESERVER_CLIENT_PREFIX": SEPAL}
    borrow_localtileserver_prefix(env)
    prefer_http_proxy(env)
    assert env["LOCALTILESERVER_DISABLE_JUPYTER_LOOPBACK"] == "1"
    assert env["VECTORTILESERVER_DISABLE_JUPYTER_LOOPBACK"] == "1"
    assert loopback_bridge_needed(env) is False


def test_no_prefix_keeps_bridge():
    env = {}
    prefer_http_proxy(env)
    assert loopback_bridge_needed(env) is True
