"""Unit tests verifying deploy/lg-smtv-mcp.service template and sandboxing directives."""

import shutil
import subprocess
from pathlib import Path

import pytest

SERVICE_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "deploy" / "lg-smtv-mcp.service"


def test_service_template_exists():
    assert SERVICE_TEMPLATE_PATH.is_file(), f"Template missing at {SERVICE_TEMPLATE_PATH}"


def test_service_template_contains_placeholders_and_hardening_directives():
    content = SERVICE_TEMPLATE_PATH.read_text(encoding="utf-8")

    # Placeholders
    assert "<User>" in content
    assert "<WorkingDirectory>" in content
    assert "<NixBinDir>" in content

    # CR-02: Bounded restart limit in [Unit]
    assert "StartLimitIntervalSec=" in content
    assert "StartLimitBurst=" in content

    # Hardening baseline (Issue #17)
    assert "NoNewPrivileges=yes" in content
    assert "PrivateTmp=yes" in content
    assert "UMask=0077" in content
    assert "ProtectSystem=full" in content
    assert "CapabilityBoundingSet=" in content


def test_systemd_analyze_verify_on_substituted_template(tmp_path: Path):
    systemd_analyze = shutil.which("systemd-analyze")
    if not systemd_analyze:
        pytest.fail("systemd-analyze not found in PATH; ensure pkgs.systemd is in testTools")

    content = SERVICE_TEMPLATE_PATH.read_text(encoding="utf-8")
    substituted = (
        content.replace("<User>", "kiskaadee")
        .replace("<WorkingDirectory>", "/home/kiskaadee/Projects/active/lg-smtv-mcp")
        .replace("<NixBinDir>", "/run/current-system/sw/bin")
    )

    test_unit_file = tmp_path / "lg-smtv-mcp.service"
    test_unit_file.write_text(substituted, encoding="utf-8")

    cmd = [systemd_analyze, "verify", str(test_unit_file)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"systemd-analyze verify failed: {res.stderr}"


def test_systemd_analyze_security_exposure_score_reduction(tmp_path: Path):
    systemd_analyze = shutil.which("systemd-analyze")
    if not systemd_analyze:
        pytest.fail("systemd-analyze not found in PATH; ensure pkgs.systemd is in testTools")

    content = SERVICE_TEMPLATE_PATH.read_text(encoding="utf-8")
    substituted = (
        content.replace("<User>", "kiskaadee")
        .replace("<WorkingDirectory>", "/home/kiskaadee/Projects/active/lg-smtv-mcp")
        .replace("<NixBinDir>", "/run/current-system/sw/bin")
    )

    test_unit_file = tmp_path / "lg-smtv-mcp.service"
    test_unit_file.write_text(substituted, encoding="utf-8")

    cmd = [systemd_analyze, "--offline=yes", "security", str(test_unit_file)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"systemd-analyze security failed: {res.stderr}"

    # Verify score is in the OK bracket (<= 5.0), down from baseline 9.0 UNSAFE
    lines = [line for line in res.stdout.splitlines() if "Overall exposure level" in line]
    assert lines, "No exposure level reported"
    score_line = lines[0]
    # Expect: → Overall exposure level for ...: X.Y OK :-)
    assert "OK" in score_line, f"Exposure level not in OK bracket: {score_line}"
