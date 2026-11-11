import pytest

from astra.tags import InvalidTag, normalize_tag, url_tag


@pytest.mark.parametrize("raw,expected", [
    ("#2Y0VPJUVJ", "#2Y0VPJUVJ"),
    ("2y0vpjuvj", "#2Y0VPJUVJ"),
    ("  #28vupjrpu ", "#28VUPJRPU"),
    ("#2YOVPJUVJ", "#2Y0VPJUVJ"),   # letter O typed instead of zero
])
def test_normalize(raw, expected):
    assert normalize_tag(raw) == expected


@pytest.mark.parametrize("raw", ["", "#", "#ABC", "#2Y0VPJUVJ!", "#12"])
def test_invalid(raw):
    with pytest.raises(InvalidTag):
        normalize_tag(raw)


def test_url_tag():
    assert url_tag("#2Y0VPJUVJ") == "%232Y0VPJUVJ"
