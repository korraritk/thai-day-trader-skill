import json
import os
import shutil
import subprocess
import sys

import pytest

from conftest import FIXTURES, ROOT

SCRIPT = os.path.join(ROOT, "scripts", "Install-Skills.ps1")
pytestmark = pytest.mark.skipif(shutil.which("powershell") is None, reason="needs Windows PowerShell")


def install(target):
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SCRIPT,
                           "-Target", str(target)], capture_output=True, text=True)


def test_installs_a_standalone_skill(tmp_path):
    r = install(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    d = tmp_path / "th-trader-scanner"
    for name in ("SKILL.md", "scan.py", "universe_file.py", "th_market.py", "setups.py", "data/tick-tables.json",
                 "data/set-holidays.json", ".installed-from-thaitraderskills"):
        assert (d / name).exists(), name
    f = os.path.join(FIXTURES, "yahoo", "spark_PTT_CPALL_1d_3mo.json")
    run = subprocess.run([sys.executable, str(d / "scan.py"), "daily", "--offline", f, "--now", "2026-11-02 09:00",
                          "--no-write", "--json", "--min-value", "0"], capture_output=True, text=True,
                         cwd=str(tmp_path))
    assert run.returncode in (0, 1), run.stderr
    assert json.loads(run.stdout)["currency"] == "THB"


def test_reinstall_replaces_its_own_copy(tmp_path):
    assert install(tmp_path).returncode == 0
    (tmp_path / "th-trader-scanner" / "stale.txt").write_text("old", encoding="utf-8")
    assert install(tmp_path).returncode == 0
    assert not (tmp_path / "th-trader-scanner" / "stale.txt").exists()


def test_refuses_a_folder_it_did_not_install(tmp_path):
    d = tmp_path / "th-trader-scanner"
    d.mkdir()
    (d / "mine.txt").write_text("keep", encoding="utf-8")
    r = install(tmp_path)
    assert r.returncode != 0
    assert (d / "mine.txt").read_text(encoding="utf-8") == "keep"
    assert not (d / "scan.py").exists()
