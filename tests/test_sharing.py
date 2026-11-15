import pytest

from litnav import sharing as S


def test_table():
    assert S.allow("open", S.SERVE_TEXT)
    assert not S.allow("licensed", S.SERVE_TEXT)
    assert S.allow("licensed", S.SERVE_META)
    assert S.allow("licensed", S.SEND_OUT)
    assert not S.allow("private", S.SERVE_META)
    for p in S.PURPOSES:
        assert S.allow("confidential", p) == (p == S.LOCAL)


def test_unknown_level_is_most_restrictive():
    assert not S.allow("licenced", S.SEND_OUT)      # typo
    assert S.allow("licenced", S.LOCAL)


def test_unknown_purpose_raises():
    with pytest.raises(ValueError):
        S.allow("open", "publish")
