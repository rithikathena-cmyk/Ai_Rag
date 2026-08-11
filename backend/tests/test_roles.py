from app.core.roles import ROLE_VALUES, Role


def test_role_values_match_enum():
    assert set(ROLE_VALUES) == {r.value for r in Role}


def test_original_roles_preserved():
    # These two values are load-bearing for existing data/API contracts —
    # renaming or removing either would break every previously-issued token
    # and the default UserModel.role column value.
    assert Role.ADMIN == "admin"
    assert Role.USER == "user"


def test_role_is_a_plain_string():
    assert Role.ADMIN == "admin"
    assert isinstance(Role.ADMIN.value, str)
