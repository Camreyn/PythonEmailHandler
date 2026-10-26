import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "examples" / "ansible" / "email_scenarios.py"
SPEC = importlib.util.spec_from_file_location("email_scenarios", RUNNER)
scenarios = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scenarios)


@pytest.mark.parametrize("name,exit_code,level,status,exception_type", [
    ("info", 0, "INFO", "info", None),
    ("success", 0, "SUCCESS", "success", None),
    ("failure", 1, "ERROR", "failure", "RuntimeError"),
    ("timeout", 124, "ERROR", "timeout", "TimeoutExpired"),
    ("smtp_timeout", 2, "INFO", "info", None),
])
def test_python_cases_work_from_another_directory(
    tmp_path, name, exit_code, level, status, exception_type
):
    completed = subprocess.run(
        [sys.executable, str(RUNNER), "--scenario", name, "--dry-run",
         "--timeout-seconds", "0.05", "--context", "Cell: Test Cell\nProfile: AppSrv01"],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=15,
    )
    assert completed.returncode == exit_code, completed.stderr
    report = json.loads(completed.stdout)
    assert report["scenario"] == name
    assert report["exit_code"] == exit_code
    assert report["level"] == level
    assert report["operation_status"] == status
    assert report["exception_type"] == exception_type
    if name == "smtp_timeout":
        assert report["notification_status"] == "failed"
        assert report["forced_smtp_timeout"]
        assert "preview" not in report
    else:
        assert report["notification_status"] == "preview"
        assert "[{}]".format(level) in report["preview"]["subject"]
        assert "Cell: Test Cell" in report["preview"]["body"]
        assert "Host: " in report["preview"]["body"]
        if exception_type:
            assert "Traceback (most recent call last)" in report["preview"]["body"]
            assert exception_type in report["preview"]["body"]


def test_preview_never_uses_the_real_smtp_connection(monkeypatch, capsys):
    connect = MagicMock(side_effect=AssertionError("A preview must not connect to SMTP"))
    monkeypatch.setattr(scenarios.email_reporter.socket, "create_connection", connect)
    assert scenarios.main(["--scenario", "success", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["notification_status"] == "preview"
    connect.assert_not_called()


def test_preview_supports_recipient_override_and_attachments(tmp_path, capsys):
    log = tmp_path / "deployment report.txt"
    log.write_text("Deployment log\n", encoding="utf-8")
    assert scenarios.main([
        "--scenario", "info", "--dry-run", "--recipient", "test@example.com",
        "--attachment", str(log),
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["preview"]["to"] == "test@example.com"
    assert report["preview"]["attachments"] == [log.name]


@pytest.mark.parametrize("name,expected_rc,expected_level", [
    ("info", 0, "INFO"), ("success", 0, "SUCCESS"), ("failure", 1, "ERROR"),
    ("timeout", 124, "ERROR"),
])
def test_live_path_calls_the_python_library(monkeypatch, capsys, name, expected_rc, expected_level):
    send = MagicMock(return_value="<accepted@example.com>")
    monkeypatch.setattr(scenarios.email_reporter, "send_email", send)
    assert scenarios.main([
        "--scenario", name, "--config-file", "/etc/was/email.ini",
        "--timeout-seconds", "0.05",
    ]) == expected_rc
    report = json.loads(capsys.readouterr().out)
    assert report["notification_status"] == "smtp_accepted"
    assert report["message_id"] == "<accepted@example.com>"
    send.assert_called_once()
    options = send.call_args.kwargs
    assert options["config_file"] == "/etc/was/email.ini"
    assert options["level"] == expected_level
    assert bool(options["exception"]) == (name in ("failure", "timeout"))


def test_delivery_failure_retains_the_original_operation_result(monkeypatch, capsys):
    error = scenarios.email_reporter.EmailError("Notification failed")
    monkeypatch.setattr(scenarios.email_reporter, "send_email", MagicMock(side_effect=error))
    assert scenarios.main(["--scenario", "failure"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["operation_status"] == "failure"
    assert report["operation_exit_code"] == 1
    assert report["exception_type"] == "RuntimeError"
    assert report["notification_status"] == "failed"


def test_bad_config_is_not_misreported_as_the_expected_smtp_timeout(tmp_path, capsys):
    assert scenarios.main([
        "--scenario", "smtp_timeout", "--dry-run",
        "--config-file", str(tmp_path / "missing.ini"),
    ]) == 2
    report = json.loads(capsys.readouterr().out)
    assert not report["forced_smtp_timeout"]
    assert "Cannot read SMTP configuration" in report["delivery_error"]


def test_smtp_timeout_supports_implicit_tls_without_connecting(tmp_path, capsys):
    config = tmp_path / "email.ini"
    config.write_text((ROOT / "email.example.ini").read_text(encoding="utf-8").replace(
        "security = starttls", "security = ssl"
    ), encoding="utf-8")
    assert scenarios.main([
        "--scenario", "smtp_timeout", "--config-file", str(config),
    ]) == 2
    assert json.loads(capsys.readouterr().out)["forced_smtp_timeout"]


def test_missing_attachment_is_a_delivery_failure(tmp_path, capsys):
    assert scenarios.main([
        "--scenario", "success", "--dry-run", "--attachment", str(tmp_path / "missing.log"),
    ]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["operation_status"] == "success"
    assert report["notification_status"] == "failed"
    assert "Cannot read email attachment" in report["delivery_error"]


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "abc", "31"])
def test_bad_timeout_is_rejected_before_an_email_attempt(monkeypatch, value):
    send = MagicMock()
    monkeypatch.setattr(scenarios.email_reporter, "send_email", send)
    with pytest.raises(SystemExit) as caught:
        scenarios.main(["--scenario", "timeout", "--timeout-seconds", value])
    assert caught.value.code == 2
    send.assert_not_called()


def test_scenario_runner_keeps_python36_syntax():
    ast.parse(RUNNER.read_text(encoding="utf-8"), feature_version=(3, 6))


def test_all_ansible_yaml_parses():
    for path in (ROOT / "examples" / "ansible").glob("*.yml"):
        assert yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("filename,scenario", [
    ("info.yml", "info"), ("forced_success.yml", "success"),
    ("forced_failure.yml", "failure"), ("operation_timeout.yml", "timeout"),
    ("smtp_timeout.yml", "smtp_timeout"),
])
def test_each_wrapper_selects_its_documented_scenario(filename, scenario):
    plays = yaml.safe_load((ROOT / "examples" / "ansible" / filename).read_text(encoding="utf-8"))
    assert plays[0]["ansible.builtin.import_playbook"] == "test_email.yml"
    assert plays[0]["vars"]["email_case"] == scenario
