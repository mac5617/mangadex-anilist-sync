import pytest

from mdal.sync.estimate import estimate


@pytest.mark.parametrize(
    "n, batch, rpm, expected",
    [(60, 10, 20, (8, 24)), (0, 10, 20, (0, 0)), (1, 10, 20, (3, 9)), (500, 10, 20, (52, 156)), (7, 0, 30, (9, 18))],
)
def test_estimate(n, batch, rpm, expected):
    assert estimate(n, batch, rpm) == expected
