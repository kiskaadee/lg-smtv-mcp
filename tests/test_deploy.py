"""Unit tests verifying deploy/lg-smtv-mcp.service template and sandboxing directives."""

import configparser
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SERVICE_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "deploy" / "lg-smtv-mcp.service"


def _parse_service_template(content: str) -> configparser.ConfigParser:
    """Parse a systemd unit file while stripping comments and preserving empty values."""
    parser = configparser.ConfigParser(
        comment_prefixes=("#", ";"),
        inline_comment_prefixes=None,
        strict=False,
        allow_no_value=True,
    )
    parser.read_string(content)
    return parser


@pytest.fixture
def systemd_analyze_bin() -> str:
    binary = shutil.which("systemd-analyze")
    if not binary:
        pytest.fail("systemd-analyze not found in PATH; ensure pkgs.systemd is in testTools")
    return binary


@pytest.fixture
def substituted_service_file(tmp_path: Path, systemd_analyze_bin: str) -> Path:
    content = SERVICE_TEMPLATE_PATH.read_text(encoding="utf-8")
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    # Use neutral non-personal values derived from tmp_path (CR-02)
    substituted = (
        content.replace("<User>", "testuser")
        .replace("<WorkingDirectory>", str(workdir))
        .replace("<NixBinDir>", str(Path(systemd_analyze_bin).parent))
    )
    test_unit_file = tmp_path / "lg-smtv-mcp.service"
    test_unit_file.write_text(substituted, encoding="utf-8")
    return test_unit_file


def test_service_template_exists():
    assert SERVICE_TEMPLATE_PATH.is_file(), f"Template missing at {SERVICE_TEMPLATE_PATH}"


def test_service_template_declares_deployment_placeholders():
    content = SERVICE_TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "<User>" in content
    assert "<WorkingDirectory>" in content
    assert "<NixBinDir>" in content


def test_service_template_bounds_restart_limit_in_unit_section():
    content = SERVICE_TEMPLATE_PATH.read_text(encoding="utf-8")
    parser = _parse_service_template(content)

    assert "Unit" in parser, "Missing [Unit] section in service template"
    unit_section = parser["Unit"]
    # CR-01: Assert exact keys and values in [Unit], not substring matches
    assert unit_section.get("startlimitintervalsec") == "60s"
    assert unit_section.get("startlimitburst") == "5"


def test_service_template_enables_sandboxing_baseline_in_service_section():
    content = SERVICE_TEMPLATE_PATH.read_text(encoding="utf-8")
    parser = _parse_service_template(content)

    assert "Service" in parser, "Missing [Service] section in service template"
    service_section = parser["Service"]

    # CR-01: Section-aware exact-value checks; comments stripped, CapabilityBoundingSet="" enforced
    assert service_section.get("nonewprivileges") == "yes"
    assert service_section.get("privatetmp") == "yes"
    assert service_section.get("umask") == "0077"
    assert service_section.get("protectsystem") == "full"
    assert service_section.get("capabilityboundingset") == ""
    assert service_section.get("protectcontrolgroups") == "yes"
    assert service_section.get("protectkernelmodules") == "yes"
    assert service_section.get("protectkerneltunables") == "yes"
    assert service_section.get("protectkernellogs") == "yes"
    assert service_section.get("protectclock") == "yes"
    assert service_section.get("protecthostname") == "yes"
    assert service_section.get("restrictrealtime") == "yes"
    assert service_section.get("restrictsuidsgid") == "yes"
    assert service_section.get("lockpersonality") == "yes"


def test_systemd_analyze_verify_succeeds_on_substituted_template(
    systemd_analyze_bin: str, substituted_service_file: Path
):
    cmd = [systemd_analyze_bin, "verify", str(substituted_service_file)]
    # CR-04: Subprocess timeout prevents CI hang
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30.0)

    # CR-01: Fail on warnings such as 'Unknown key' or 'Unknown lvalue' that exit 0
    assert "Unknown key" not in res.stderr, f"Unknown key in template: {res.stderr}"
    assert "Unknown lvalue" not in res.stderr, f"Unknown lvalue in template: {res.stderr}"
    assert res.returncode == 0, f"systemd-analyze verify failed: {res.stderr}"


def test_systemd_analyze_security_reports_ok_exposure_bracket(
    systemd_analyze_bin: str, substituted_service_file: Path
):
    cmd = [systemd_analyze_bin, "--offline=yes", "security", str(substituted_service_file)]
    # CR-04: Subprocess timeout prevents CI hang
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30.0)
    assert res.returncode == 0, f"systemd-analyze security failed: {res.stderr}"

    # CR-05: Regex-parse the numeric exposure score and assert it satisfies the OK bracket (<= 5.0)
    lines = [line for line in res.stdout.splitlines() if "Overall exposure level" in line]
    assert lines, "No exposure level reported by systemd-analyze security"
    score_line = lines[0]

    match = re.search(r"Overall exposure level.*:\s*([0-9]+\.[0-9]+)\s+OK", score_line)
    assert match is not None, f"Could not parse numeric score and OK status from: {score_line}"

    score = float(match.group(1))
    assert score <= 5.0, f"Exposure score {score} exceeds OK bracket threshold 5.0"
