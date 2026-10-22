import ast
import importlib.util
import io
import smtplib
import ssl
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "email_reporter.py"
SPEC = importlib.util.spec_from_file_location("email_reporter", MODULE_PATH)
email_reporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(email_reporter)


@pytest.fixture
def config_factory(tmp_path):
    def create(**overrides):
        settings = {
            "host": "smtp.example.com",
            "port": "25",
            "security": "none",
            "from_address": "automation@example.com",
            "to": "ops@example.com, support@example.com",
            "subject_prefix": "WAS/BAW",
            "timeout_seconds": "10",
        }
        settings.update(overrides)
        path = tmp_path / "email.ini"
        path.write_text(
            "[smtp]\n" + "".join("{} = {}\n".format(k, v) for k, v in settings.items()),
            encoding="utf-8",
        )
        return path
    return create


@pytest.fixture
def smtp_client(monkeypatch):
    client = MagicMock()
    client.__enter__.return_value = client
    client.send_message.return_value = {}
    smtp = MagicMock(return_value=client)
    smtp_ssl = MagicMock(return_value=client)
    monkeypatch.setattr(email_reporter.smtplib, "SMTP", smtp)
    monkeypatch.setattr(email_reporter.smtplib, "SMTP_SSL", smtp_ssl)
    return client, smtp, smtp_ssl


def test_plain_relay_report(config_factory, smtp_client):
    client, smtp, smtp_ssl = smtp_client
    message_id = email_reporter.send_email(
        "Health check", "Cell: ProductionCell\nAll JVMs are running.",
        config_factory(), level="info",
    )
    smtp.assert_called_once_with(host="smtp.example.com", port=25, timeout=10.0)
    smtp_ssl.assert_not_called()
    client.login.assert_not_called()
    client.starttls.assert_not_called()
    message = client.send_message.call_args.args[0]
    assert message["Message-ID"] == message_id
    assert message["Date"]
    assert "[WAS/BAW][INFO][" in str(message["Subject"])
    assert "Health check" in str(message["Subject"])
    assert "Host: " in message.get_content()
    assert "Time (UTC): " in message.get_content()
    assert "Cell: ProductionCell" in message.get_content()
    assert client.send_message.call_args.kwargs == {
        "from_addr": "automation@example.com",
        "to_addrs": ["ops@example.com", "support@example.com"],
    }


def test_starttls_authenticates_only_after_upgrade(config_factory, smtp_client, monkeypatch):
    client, smtp, smtp_ssl = smtp_client
    context = ssl.create_default_context()
    create_context = MagicMock(return_value=context)
    monkeypatch.setattr(email_reporter.ssl, "create_default_context", create_context)
    monkeypatch.setenv("REPORT_SMTP_PASSWORD", "environment-secret")
    email_reporter.send_email("Check", "OK", config_factory(
        security="starttls", port=587, username="automation@example.com",
        password="unused-password", password_env="REPORT_SMTP_PASSWORD",
    ))
    create_context.assert_called_once_with(cafile=None)
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert client.method_calls[:3] == [
        call.starttls(context=context), call.ehlo(),
        call.login("automation@example.com", "environment-secret"),
    ]
    smtp_ssl.assert_not_called()


def test_implicit_tls_and_literal_password(config_factory, smtp_client):
    client, smtp, smtp_ssl = smtp_client
    email_reporter.send_email("Check", "OK", config_factory(
        security="ssl", port=465, username="automation@example.com", password="p%ssword",
    ))
    smtp.assert_not_called()
    client.starttls.assert_not_called()
    client.login.assert_called_once_with("automation@example.com", "p%ssword")
    options = smtp_ssl.call_args.kwargs
    assert options["port"] == 465
    assert options["context"].check_hostname
    assert options["context"].verify_mode == ssl.CERT_REQUIRED


def test_internal_ca_path_is_relative_to_config(config_factory, smtp_client, monkeypatch):
    config = config_factory(security="starttls", ca_file="certs/internal-ca.pem")
    create_context = MagicMock()
    monkeypatch.setattr(email_reporter.ssl, "create_default_context", create_context)
    email_reporter.send_email("Check", "OK", config)
    create_context.assert_called_once_with(cafile=str(config.parent / "certs/internal-ca.pem"))


@pytest.mark.parametrize("error", [
    smtplib.SMTPNotSupportedError("STARTTLS unavailable"),
    ssl.SSLCertVerificationError("certificate mismatch"),
])
def test_failed_starttls_does_not_send_or_login(config_factory, smtp_client, error):
    client, smtp, smtp_ssl = smtp_client
    client.starttls.side_effect = error
    with pytest.raises(email_reporter.EmailError) as caught:
        email_reporter.send_email("Check", "OK", config_factory(
            security="starttls", username="user", password="secret",
        ))
    assert caught.value.__cause__ is error
    client.login.assert_not_called()
    client.send_message.assert_not_called()
    smtp_ssl.assert_not_called()


def test_authentication_failure_does_not_send(config_factory, smtp_client):
    client, _, _ = smtp_client
    error = smtplib.SMTPAuthenticationError(535, b"Bad credentials")
    client.login.side_effect = error
    with pytest.raises(email_reporter.EmailError) as caught:
        email_reporter.send_email("Check", "OK", config_factory(
            security="starttls", username="user", password="secret",
        ))
    assert caught.value.__cause__ is error
    client.send_message.assert_not_called()


def test_network_failure_is_reported(config_factory, smtp_client):
    _, smtp, _ = smtp_client
    error = TimeoutError("SMTP connection timed out")
    smtp.side_effect = error
    with pytest.raises(email_reporter.EmailError, match="TimeoutError") as caught:
        email_reporter.send_email("Check", "OK", config_factory())
    assert caught.value.__cause__ is error


@pytest.mark.parametrize("settings", [
    {"host": ""}, {"port": "0"}, {"port": "65536"}, {"port": "abc"},
    {"security": "tls-maybe"}, {"timeout_seconds": "0"},
    {"timeout_seconds": "nan"}, {"timeout_seconds": "inf"},
    {"from_address": "not-an-address"},
    {"from_address": "one@example.com,two@example.com"},
    {"to": ""}, {"to": "invalid-address"},
    {"username": "user"}, {"password": "orphaned-secret"},
    {"password_env": "ORPHANED_ENVIRONMENT_VARIABLE"},
])
def test_bad_config_does_not_connect(config_factory, smtp_client, settings):
    _, smtp, smtp_ssl = smtp_client
    with pytest.raises(email_reporter.EmailError):
        email_reporter.send_email("Check", "OK", config_factory(**settings))
    smtp.assert_not_called()
    smtp_ssl.assert_not_called()


@pytest.mark.parametrize("contents", ["", "[other]\nhost = smtp.example.com\n", "[smtp]\nhost = x\n"])
def test_incomplete_config_is_reported(tmp_path, smtp_client, contents):
    config = tmp_path / "email.ini"
    config.write_text(contents, encoding="utf-8")
    with pytest.raises(email_reporter.EmailError, match="Cannot read SMTP configuration"):
        email_reporter.send_email("Check", "OK", config)


def test_missing_config_is_reported(tmp_path, smtp_client):
    with pytest.raises(email_reporter.EmailError, match="Cannot read SMTP configuration"):
        email_reporter.send_email("Check", "OK", tmp_path / "missing.ini")


def test_missing_password_environment_does_not_fall_back(config_factory, smtp_client, monkeypatch):
    _, smtp, _ = smtp_client
    monkeypatch.delenv("REPORT_SMTP_PASSWORD", raising=False)
    with pytest.raises(email_reporter.EmailError, match="environment variable is missing"):
        email_reporter.send_email("Check", "OK", config_factory(
            username="user", password="must-not-be-used", password_env="REPORT_SMTP_PASSWORD",
        ))
    smtp.assert_not_called()


def test_default_config_uses_module_directory(config_factory, smtp_client, monkeypatch, tmp_path):
    config = config_factory()
    monkeypatch.setattr(email_reporter, "__file__", str(config.parent / "email_reporter.py"))
    elsewhere = tmp_path / "working-directory"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert email_reporter.send_email("Check", "OK")


@pytest.mark.parametrize("recipients", [
    "other@example.com; other@example.com",
    ["other@example.com", "other@example.com"],
])
def test_override_replaces_defaults_and_removes_duplicates(config_factory, smtp_client, recipients):
    client, _, _ = smtp_client
    email_reporter.send_email("Check", "OK", config_factory(to=""), recipients=recipients)
    assert client.send_message.call_args.kwargs["to_addrs"] == ["other@example.com"]
    assert client.send_message.call_args.args[0]["To"] == "other@example.com"


@pytest.mark.parametrize("subject,prefix,recipients", [
    ("Status\nBcc: attacker@example.com", "WAS", "ops@example.com"),
    ("Status", "WAS\n  Bcc: attacker@example.com", "ops@example.com"),
    ("Status", "WAS", "ops@example.com\r\nBcc: attacker@example.com"),
])
def test_header_injection_is_rejected(config_factory, smtp_client, subject, prefix, recipients):
    _, smtp, _ = smtp_client
    with pytest.raises(email_reporter.EmailError):
        email_reporter.send_email(subject, "OK", config_factory(subject_prefix=prefix), recipients=recipients)
    smtp.assert_not_called()


def test_unknown_severity_is_rejected(config_factory, smtp_client):
    with pytest.raises(email_reporter.EmailError, match="Unsupported report level"):
        email_reporter.send_email("Check", "OK", config_factory(), level="UNRECOGNIZED")


def test_exception_traceback_is_in_report(config_factory, smtp_client):
    client, _, _ = smtp_client
    try:
        raise RuntimeError("JVM server1 is stopped")
    except RuntimeError as error:
        email_reporter.send_email("JVM failed", "Profile: AppSrv01", config_factory(),
                                  level="ERROR", exception=error)
    report = client.send_message.call_args.args[0].get_content()
    assert "Traceback (most recent call last)" in report
    assert "RuntimeError: JVM server1 is stopped" in report
    assert "Level: ERROR" in report


def test_email_failure_can_preserve_original_exception(config_factory, smtp_client):
    client, _, _ = smtp_client
    client.send_message.side_effect = smtplib.SMTPServerDisconnected("offline")
    original = RuntimeError("Original WAS command failed")
    with pytest.raises(RuntimeError) as caught:
        try:
            raise original
        except RuntimeError as error:
            try:
                email_reporter.send_email("Failure", "Check failed", config_factory(), exception=error)
            except email_reporter.EmailError:
                pass
            raise
    assert caught.value is original


def test_missing_attachment_prevents_delivery(config_factory, smtp_client, tmp_path):
    _, smtp, _ = smtp_client
    with pytest.raises(email_reporter.EmailError, match="Cannot read email attachment"):
        email_reporter.send_email("Check", "OK", config_factory(), attachments=tmp_path / "missing.log")
    smtp.assert_not_called()


def test_all_recipients_refused_preserves_details(config_factory, smtp_client):
    client, _, _ = smtp_client
    refused = {"ops@example.com": (550, b"Unknown user"), "support@example.com": (550, b"Unknown user")}
    client.send_message.side_effect = smtplib.SMTPRecipientsRefused(refused)
    with pytest.raises(email_reporter.EmailError, match="every recipient") as caught:
        email_reporter.send_email("Check", "OK", config_factory())
    assert caught.value.refused_recipients == refused
    client.send_message.assert_called_once()


class _SMTPWire:
    """Socket substitute; real smtplib still runs EHLO, MAIL, RCPT, and DATA."""

    def __init__(self, reject_second=False):
        second_recipient = b"550 Unknown recipient" if reject_second else b"250 OK"
        self.replies = io.BytesIO(b"\r\n".join([
            b"220 localhost test SMTP", b"250 OK", b"250 OK", b"250 OK",
            second_recipient, b"354 End with dot", b"250 Queued", b"221 Bye", b"",
        ]))
        self.sent = []
        self.closed = False

    def makefile(self, mode):
        assert mode == "rb"
        return self.replies

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True

    @property
    def messages(self):
        messages = []
        for data in self.sent:
            if data.endswith(b"\r\n.\r\n"):
                lines = data[:-3].split(b"\r\n")
                messages.append(b"\r\n".join(
                    line[1:] if line.startswith(b"..") else line for line in lines
                ))
        return messages


@pytest.fixture
def smtp_wire(monkeypatch):
    def create(reject_second=False):
        wire = _SMTPWire(reject_second=reject_second)
        monkeypatch.setattr(smtplib.SMTP, "_get_socket", lambda *args: wire)
        return wire
    return create


def test_smtp_protocol_with_unicode_and_attachments(config_factory, smtp_wire, tmp_path):
    wire = smtp_wire()
    log = tmp_path / "SystemOut.log"
    log.write_bytes(b"JVM started\n")
    dump = tmp_path / "heap.bin"
    dump.write_bytes(b"\x00\xff\x7f")
    message_id = email_reporter.send_email(
        "BAW caf\u00e9 deployment", "Application is healthy.\n.dot-prefixed line\ncaf\u00e9",
        config_factory(),
        attachments=[log, dump],
    )
    assert len(wire.messages) == 1
    assert wire.closed
    commands = [command.lower() for command in wire.sent]
    assert any(command.startswith(b"ehlo ") for command in commands)
    assert b"mail from:<automation@example.com>\r\n" in commands
    assert b"rcpt to:<ops@example.com>\r\n" in commands
    message = BytesParser(policy=policy.default).parsebytes(wire.messages[0])
    assert message["Message-ID"] == message_id
    assert "caf\u00e9" in message["Subject"]
    body = message.get_body(preferencelist=("plain",)).get_content()
    assert ".dot-prefixed line" in body
    assert "caf\u00e9" in body
    attachments = list(message.iter_attachments())
    assert [part.get_filename() for part in attachments] == ["SystemOut.log", "heap.bin"]
    assert attachments[0].get_payload(decode=True) == log.read_bytes()
    assert attachments[1].get_payload(decode=True) == dump.read_bytes()
    assert attachments[1].get_content_type() == "application/octet-stream"


def test_smtp_partial_rejection_is_reported_without_retry(config_factory, smtp_wire):
    wire = smtp_wire(reject_second=True)
    with pytest.raises(email_reporter.EmailError, match="some recipients") as caught:
        email_reporter.send_email(
            "Check", "OK", config_factory(),
            recipients=["accepted@example.com", "rejected@example.com"],
        )
    assert caught.value.refused_recipients["rejected@example.com"][0] == 550
    assert len(wire.messages) == 1


def test_helper_uses_python36_syntax():
    ast.parse(MODULE_PATH.read_text(encoding="utf-8"), feature_version=(3, 6))
