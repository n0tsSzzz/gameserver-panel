from gamehost_api.core.security import hash_password, verify_password


def test_hash_then_verify_returns_true() -> None:
    h = hash_password("hunter22hunter22")
    assert h != "hunter22hunter22"
    assert verify_password(h, "hunter22hunter22") is True


def test_verify_with_wrong_password_returns_false() -> None:
    h = hash_password("correct horse battery staple")
    assert verify_password(h, "incorrect horse battery staple") is False
