import os

import pytest

from conftest import FIXTURES
from universe_file import UniverseError, is_common_share, read_listed_companies

EXPECTED = [("PTT", "SET"), ("AU", "mai"), ("L&E", "SET")]


def path(name):
    return os.path.join(FIXTURES, "universe", name)


@pytest.mark.parametrize("name", ["listed.html.xls", "listed_th_TH.html.xls", "listed.xlsx", "listed_cp874.csv", "listed_utf8bom.csv"])
def test_reads_each_format(name):
    assert read_listed_companies(path(name)) == EXPECTED


def test_old_binary_xls_says_what_to_do():
    with pytest.raises(UniverseError, match="save it as .xlsx or .csv"):
        read_listed_companies(path("listed_biff.xls"))


def test_no_header_row_is_an_error(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(UniverseError, match="Symbol"):
        read_listed_companies(str(p))


@pytest.mark.parametrize("symbol,common", [("PTT", True), ("L&E", True), ("BBL-R", True), ("PTT-W1", False),
                                           ("JMART-W4", False), ("TRUE-F", False), ("BANP19C2303A", False)])
def test_is_common_share(symbol, common):
    assert is_common_share(symbol) is common
