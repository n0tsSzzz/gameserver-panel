from gamehost_api.core.logging import redact_secrets


def test_redact_secrets_masks_known_keys_recursively() -> None:
    event = {
        "msg": "login",
        "password": "hunter22",
        "Authorization": "Bearer xyz",
        "nested": {"refresh_token": "abc", "fine": "value"},
        "list": [{"cookie": "x"}, {"ok": 1}],
    }
    out = redact_secrets(None, "info", event)
    assert out["password"] == "***"
    assert out["Authorization"] == "***"
    assert out["nested"]["refresh_token"] == "***"
    assert out["nested"]["fine"] == "value"
    assert out["list"][0]["cookie"] == "***"
    assert out["list"][1]["ok"] == 1
