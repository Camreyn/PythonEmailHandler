#!/usr/bin/env python3
"""Exercise email_reporter from Ansible; Python owns every notification.

Use --dry-run to run the real library with a mocked SMTP transport. The
smtp_timeout scenario always injects a connection timeout and sends no mail.
"""

import argparse
import json
import math
import socket
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))

import email_reporter  # noqa: E402


SCENARIOS = ("info", "success", "failure", "timeout", "smtp_timeout")


def positive_seconds(value):
    """Reject invalid deadlines before starting the demonstration child."""
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timeout must be a number") from error
    if not math.isfinite(seconds) or not 0 < seconds <= 30:
        raise argparse.ArgumentTypeError("timeout must be greater than 0 and at most 30 seconds")
    return seconds


def operation_result(scenario, timeout_seconds):
    """Run a safe demonstration operation and retain its original exception."""
    try:
        if scenario == "failure":
            raise RuntimeError("Forced operation failure for the email reporting test")
        if scenario == "timeout":
            # subprocess.run kills and reaps this child after the deadline.
            # It does not execute or change any WAS/BAW services.
            subprocess.run(
                [sys.executable, "-c", "import time; time.sleep({!r})".format(timeout_seconds + 5)],
                timeout=timeout_seconds,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            raise RuntimeError("The timeout demonstration unexpectedly completed")
        if scenario == "success":
            return "success", "SUCCESS", "Forced operation completed successfully.", None, 0
        return "info", "INFO", "Informational server and WAS/BAW reporting test.", None, 0
    except subprocess.TimeoutExpired as error:
        return "timeout", "ERROR", "Operation exceeded its {:.3f}-second deadline.".format(
            timeout_seconds
        ), error, 124
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        return "failure", "ERROR", str(error), error, 1


def run_scenario(args):
    """Return (exit_code, JSON report), preserving task vs mail failure status."""
    status, level, details, error, operation_rc = operation_result(
        args.scenario, args.timeout_seconds
    )
    config_file = args.config_file or str(REPOSITORY_ROOT / (
        "email.example.ini" if args.dry_run else "email.ini"
    ))
    subject = "Email reporter test: {}".format(args.scenario)
    body = "Scenario: {}\nOperation status: {}\n{}".format(args.scenario, status, details)
    if args.context:
        body += "\n\n" + args.context
    result = {
        "scenario": args.scenario,
        "operation_status": status,
        "operation_exit_code": operation_rc,
        "level": level,
        "dry_run": args.dry_run,
        "notification_status": "pending",
        "forced_smtp_timeout": False,
        "exception_type": type(error).__name__ if error is not None else None,
    }

    preview = None
    try:
        with ExitStack() as stack:
            if args.scenario == "smtp_timeout":
                # Deterministic error injection: no host or SMTP endpoint is contacted.
                for smtp_class in ("SMTP", "SMTP_SSL"):
                    stack.enter_context(patch.object(
                        email_reporter.smtplib, smtp_class,
                        side_effect=socket.timeout("Forced SMTP connection timeout"),
                    ))
            elif args.dry_run:
                preview = MagicMock()
                preview.__enter__.return_value = preview
                preview.send_message.return_value = {}
                for smtp_class in ("SMTP", "SMTP_SSL"):
                    stack.enter_context(patch.object(
                        email_reporter.smtplib, smtp_class, return_value=preview
                    ))

            result["message_id"] = email_reporter.send_email(
                subject=subject,
                body=body,
                config_file=config_file,
                level=level,
                recipients=args.recipient,
                attachments=args.attachment,
                exception=error,
            )
            result["notification_status"] = "preview" if args.dry_run else "smtp_accepted"
            if preview is not None:
                message = preview.send_message.call_args[0][0]
                result["preview"] = {
                    "subject": str(message["Subject"]),
                    "to": str(message["To"]),
                    "body": message.get_body(preferencelist=("plain",)).get_content(),
                    "attachments": [part.get_filename() for part in message.iter_attachments()],
                }
    except email_reporter.EmailError as delivery_error:
        # Keep the original operation status even when its notification fails.
        cause = delivery_error.__cause__
        result["notification_status"] = "failed"
        result["delivery_error"] = str(delivery_error)
        result["delivery_error_type"] = type(cause).__name__ if cause else type(delivery_error).__name__
        result["forced_smtp_timeout"] = (
            args.scenario == "smtp_timeout" and isinstance(cause, socket.timeout)
        )
        result["exit_code"] = 2
        return 2, result

    result["exit_code"] = operation_rc
    return operation_rc, result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument("--config-file", help="INI path on the machine running Python")
    parser.add_argument("--dry-run", action="store_true", help="Mock SMTP; do not send mail")
    parser.add_argument("--timeout-seconds", type=positive_seconds, default=0.2)
    parser.add_argument("--context", default="", help="Cell, profile, JVM, or other report details")
    parser.add_argument("--recipient", action="append", help="Override recipients; repeat as needed")
    parser.add_argument("--attachment", action="append", help="Attach a file; repeat as needed")
    args = parser.parse_args(argv)
    exit_code, report = run_scenario(args)
    print(json.dumps(report, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
