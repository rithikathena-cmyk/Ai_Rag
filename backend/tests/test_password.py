from app.services.auth.password import hash_password, verify_password


def test_roundtrip_verifies():
    encoded = hash_password("correct-horse-battery-staple")
    assert verify_password("correct-horse-battery-staple", encoded)


def test_wrong_password_fails():
    encoded = hash_password("correct-horse-battery-staple")
    assert not verify_password("wrong-password", encoded)


def test_hash_is_salted_differently_each_time():
    a = hash_password("same-password")
    b = hash_password("same-password")
    assert a != b
    assert verify_password("same-password", a)
    assert verify_password("same-password", b)


def test_malformed_hash_fails_closed():
    assert not verify_password("anything", "not-a-valid-hash")
    assert not verify_password("anything", "pbkdf2_sha256$not-enough-parts")


def test_unknown_algorithm_fails_closed():
    assert not verify_password("anything", "bcrypt$12$salt$digest")
