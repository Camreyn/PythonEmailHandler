"""Reusable SMTP reporting for Python 3.6+; no third-party dependencies.

Copy this module into a script's directory and import ``send_email``. By
default, configuration is read from ``email.ini`` beside this module.
"""

import configparser
import math
import mimetypes
import os
import smtplib
import socket
import ssl
import traceback
from datetime import datetime, timezone
from email.errors import HeaderParseError
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path


class EmailError(RuntimeError):
    """Configuration, attachment, or SMTP failure.

    ``refused_recipients`` maps rejected addresses to SMTP (code, response)
    tuples. On partial rejection, other recipients already accepted the email;
    retrying the entire notification can create duplicates.
    """

    def __init__(self, message, refused_recipients=None):
        super().__init__(message)
        self.refused_recipients = refused_recipients or {}


def _addresses(values, field):
    """Validate bare email addresses and remove duplicates in input order."""
    if isinstance(values, str):
        values = values.replace(";", ",").split(",")
    addresses = []
    for value in values:
        value = value.strip()
        try:
            address = Address(addr_spec=value)
        except (ValueError, HeaderParseError) as error:
            raise EmailError("Invalid email address in {}".format(field)) from error
        if not address.username or not address.domain:
            raise EmailError("Use complete email addresses in {}".format(field))
        if value not in addresses:
            addresses.append(value)
    if not addresses:
        raise EmailError("At least one email address is required in {}".format(field))
    return addresses


def _read_config(config_file):
    """Load and validate configuration before opening a network connection."""
    path = (
        Path(config_file)
        if config_file is not None
        else Path(__file__).with_name("email.ini")
    )
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
        section = parser["smtp"]
        host = section["host"].strip()
        security = section.get("security", "starttls").strip().lower()
        port = section.getint("port", fallback=587)
        timeout = section.getfloat("timeout_seconds", fallback=15)
        sender = _addresses(section["from_address"], "from_address")
        recipients = section.get("to", "")
    except (OSError, UnicodeError, configparser.Error, KeyError, ValueError) as error:
        raise EmailError("Cannot read SMTP configuration: {}".format(path)) from error
    if not host:
        raise EmailError("SMTP host must not be empty")
    if security not in ("none", "starttls", "ssl"):
        raise EmailError("SMTP security must be none, starttls, or ssl")
    if not 1 <= port <= 65535:
        raise EmailError("SMTP port must be between 1 and 65535")
    if not math.isfinite(timeout) or timeout <= 0:
        raise EmailError("SMTP timeout_seconds must be a positive finite number")
    if len(sender) != 1:
        raise EmailError("from_address must contain exactly one email address")

    username = section.get("username", "").strip()
    password_env = section.get("password_env", "").strip()
    password = section.get("password", "")
    if username and password_env:
        password = os.environ.get(password_env, "")
        if not password:
            raise EmailError(
                "SMTP password environment variable is missing: {}".format(password_env)
            )
    if username and not password:
        raise EmailError("SMTP username requires password or password_env")
    if not username and (password or password_env):
        raise EmailError("SMTP password or password_env requires username")

    ca_file = section.get("ca_file", "").strip()
    if ca_file:
        ca_path = Path(ca_file)
        ca_file = str(ca_path if ca_path.is_absolute() else path.parent / ca_path)
    return {
        "host": host,
        "port": port,
        "timeout": timeout,
        "security": security,
        "username": username,
        "password": password,
        "sender": sender[0],
        "recipients": recipients,
        "subject_prefix": section.get("subject_prefix", "WAS/BAW").strip(),
        "ca_file": ca_file or None,
    }


def send_email(
    subject, body, config_file=None, *, level="INFO", recipients=None,
    attachments=None, exception=None
):
    """Send a plain-text report and return its generated Message-ID.

    Args:
        subject: Short subject without newlines.
        body: Report text; include cell, node, profile, JVM, or application details.
        config_file: INI path; defaults to email.ini beside this module.
        level: DEBUG, INFO, SUCCESS, WARNING, ERROR, or CRITICAL.
        recipients: Optional bare address, comma-separated string, or iterable
            of bare addresses. Replaces the config's ``to`` setting.
        attachments: Optional iterable of file paths, relative to the caller's
            working directory or absolute. A single path is also accepted.
        exception: Optional caught exception whose traceback should be included.

    Raises:
        EmailError: Invalid configuration, unreadable attachment, SMTP/network
            failure, or rejection of any recipient. The original exception is
            available as __cause__ when applicable.

    Success means the SMTP server accepted every recipient, not confirmation
    of inbox delivery. No automatic retries are performed.
    """
    config = _read_config(config_file)
    to_addresses = _addresses(
        config["recipients"] if recipients is None else recipients, "to"
    )
    level = level.strip().upper()
    if level not in ("DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"):
        raise EmailError("Unsupported report level: {}".format(level))

    hostname = socket.gethostname()
    timestamp = datetime.now(timezone.utc)
    prefix = config["subject_prefix"]
    full_subject = "{}[{}][{}] {}".format(
        "[{}]".format(prefix) if prefix else "", level, hostname, subject
    )
    if "\r" in full_subject or "\n" in full_subject:
        raise EmailError("Email subject and subject_prefix must not contain newlines")

    report = "Host: {}\nTime (UTC): {}\nLevel: {}\n\n{}".format(
        hostname, timestamp.isoformat(), level, body
    )
    if exception is not None:
        report += "\n\nTraceback:\n" + "".join(traceback.format_exception(
            type(exception), exception, exception.__traceback__
        ))

    message = EmailMessage()
    message["From"] = config["sender"]
    message["To"] = ", ".join(to_addresses)
    message["Subject"] = full_subject
    message["Date"] = format_datetime(timestamp)
    message["Message-ID"] = make_msgid()
    message.set_content(report)

    if isinstance(attachments, (str, os.PathLike)):
        attachments = [attachments]
    for attachment in attachments or ():
        path = Path(attachment)
        content_type, encoding = mimetypes.guess_type(str(path))
        if content_type is None or encoding is not None:
            content_type = "application/octet-stream"
        maintype, subtype = content_type.split("/", 1)
        try:
            content = path.read_bytes()
        except OSError as error:
            raise EmailError("Cannot read email attachment: {}".format(path)) from error
        message.add_attachment(
            content, maintype=maintype, subtype=subtype, filename=path.name
        )

    connection_options = {
        "host": config["host"], "port": config["port"], "timeout": config["timeout"]
    }
    try:
        context = None
        if config["security"] != "none":
            context = ssl.create_default_context(cafile=config["ca_file"])
        smtp_class = smtplib.SMTP
        if config["security"] == "ssl":
            smtp_class = smtplib.SMTP_SSL
            connection_options["context"] = context
        with smtp_class(**connection_options) as client:
            if config["security"] == "starttls":
                client.starttls(context=context)
                client.ehlo()
            if config["username"]:
                client.login(config["username"], config["password"])
            refused = client.send_message(
                message, from_addr=config["sender"], to_addrs=to_addresses
            )
    except smtplib.SMTPRecipientsRefused as error:
        raise EmailError("SMTP server refused every recipient", error.recipients) from error
    except (OSError, smtplib.SMTPException, ValueError) as error:
        raise EmailError("SMTP delivery failed at {}:{} ({})".format(
            config["host"], config["port"], type(error).__name__
        )) from error
    if refused:
        raise EmailError(
            "SMTP server refused some recipients; others already accepted the email. "
            "Do not retry the entire recipient list automatically.", refused
        )
    return str(message["Message-ID"])
