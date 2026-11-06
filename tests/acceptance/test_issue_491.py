"""Akzeptanztests: einheitliche Seitentitel."""
import re

import pytest

from tests.test_web_tracks_ui import PASSWORD, client_for


@pytest.mark.parametrize("pfad", ["/", "/tracks", "/digest", "/mix", "/kaufliste", "/rejected", "/duplicates", "/intake"])
def test_titel_format(tmp_path, pfad):
    r = client_for(tmp_path).get(pfad, auth=("sc", PASSWORD))
    titel = re.search(r"<title>(.*?)</title>", r.text, re.S).group(1).strip()
    assert titel.endswith(" – sc-digger")
    assert not titel.startswith("sc-digger")