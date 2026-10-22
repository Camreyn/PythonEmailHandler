# PythonEmailHandler

`email_reporter.py` provides `send_email()` for server, WAS ND, and BAW
reports using Python 3.6 or newer and the standard library. Copy the module into
the same directory as any Python script that needs it; no pip installation is
required. Run it with the server's Python 3 interpreter. The Jython interpreter
embedded in traditional WAS `wsadmin` does not run this Python 3 module.

## Quick start

Clone this repository:

```sh
git clone https://github.com/Camreyn/PythonEmailHandler.git
cd PythonEmailHandler
```

Copy `email.example.ini` to `email.ini` and enter your SMTP server, sender,
and recipient settings. Copy `email_reporter.py` and the configured `email.ini`
beside the Python script that will use them. Then call:

```python
from email_reporter import send_email

send_email(
    subject="WAS ND status report",
    body="Profile: AppSrv01\nServer: server1\nStatus: RUNNING",
    level="INFO",
)
```

The runtime requires only Python's standard library. There is no package
installation step for using the helper.

## Configure

Copy `email.example.ini` as `email.ini` beside the copied module and set
`host`, `port`, `security`, `from_address`, and `to` for your SMTP server. The
default path is relative to the module, so it works with cron jobs regardless
of their working directory. Alternatively, pass `config_file` explicitly; a
relative explicit path is relative to the process's working directory.

The filled-in `email.ini` is ignored by Git. Only the example configuration
is committed. You can also keep configuration outside the repository, such as
`/etc/was-automation/email.ini`, and pass its path explicitly.

For a relay that does not require authentication:

```ini
[smtp]
host = smtp.internal.example.com
port = 25
security = none
from_address = was-automation@example.com
to = was-operations@example.com, application-support@example.com
subject_prefix = WAS/BAW
timeout_seconds = 15
```

For authenticated SMTP, use `security = starttls` (commonly port 587) or
`security = ssl` (commonly port 465). Use your server's actual settings. Set
`username` and either `password` or `password_env`. For example:

```ini
username = was-automation@example.com
password_env = SMTP_PASSWORD
```

The environment variable must be present in the process running the script,
including cron or service environments. If set, `password_env` takes precedence
over `password`; a missing variable is an error. Leave all three settings blank
for an unauthenticated relay. ConfigParser interpolation is disabled, so a
literal password containing `%` works. Keep a credential-bearing config readable
only by the service account, for example `chmod 600 email.ini` on Linux.

TLS verifies the server certificate and hostname. For a private CA, set
`ca_file` to its PEM bundle. Relative CA paths resolve beside the INI file.
STARTTLS failures stop the send before authentication or message transmission.

## Send information or warnings

```python
from email_reporter import send_email

message_id = send_email(
    subject="Application health check completed",
    body=(
        "Cell: ProductionCell\n"
        "Node: Node01\n"
        "Profile: AppSrv01\n"
        "JVM: server1\n"
        "WAS ND and BAW health checks passed."
    ),
    level="INFO",
)

send_email(
    "High JVM heap usage",
    "Cell: ProductionCell\nJVM: server1\nHeap usage: 91%",
    level="WARNING",
)
```

Every report includes the local hostname, UTC timestamp, and severity.
Subjects look like `[WAS/BAW][WARNING][hostname] High JVM heap usage`.
Supported levels are `DEBUG`, `INFO`, `SUCCESS`, `WARNING`, `ERROR`, and
`CRITICAL`. Supply the actual check results in `body`; the helper sends reports
and does not itself inspect or modify WebSphere or BAW.

## Report a failed WAS command without hiding its original error

```python
import logging
import subprocess

from email_reporter import EmailError, send_email

logger = logging.getLogger(__name__)

try:
    result = subprocess.run(
        ["/opt/WebSphere/AppServers/profiles/AppSrv01/bin/serverStatus.sh", "-all"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        timeout=120,
        check=True,
    )
    # Inspect result.stdout for your expected runtime states as well.
    # A zero exit status alone does not establish application health.
except (OSError, subprocess.SubprocessError) as error:
    logger.exception("WAS status command failed")
    output = getattr(error, "stdout", None) or "No command output available."
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    try:
        send_email(
            subject="WAS status command failed",
            body="Profile: AppSrv01\n\nCommand output:\n" + output,
            level="ERROR",
            exception=error,
        )
    except EmailError:
        logger.exception("Could not send the error notification")
    raise
```

Pass a caught exception as `exception=error` to append its traceback. The nested
handler logs an email failure and the final `raise` preserves the original
command failure.

## Attach selected logs or override recipients

```python
from email_reporter import send_email

send_email(
    subject="BAW deployment report",
    body="Change: CHG001234\nApplication: Claims\nDeployment completed.",
    level="SUCCESS",
    config_file="/etc/was-automation/email.ini",
    recipients=["baw-support@example.com"],
    attachments=["/var/tmp/baw-deployment-report.txt"],
)
```

`recipients` replaces the configured `to`; use bare addresses, either a list
or a comma-separated string. Attachments accept one path or an iterable of
paths. Relative attachment paths resolve from the working directory. Files
are read into memory before connecting; select the relevant log excerpts.
An unreadable attachment prevents the send.

`send_email()` returns a Message-ID when the SMTP server accepts every
recipient. SMTP acceptance does not confirm inbox delivery. Configuration,
file, connection, authentication, TLS, and recipient rejection failures raise
`EmailError`. Its `__cause__` retains the underlying exception when applicable.
`EmailError.refused_recipients` contains recipient rejection details. Partial
rejection raises an error even though other recipients already accepted the
message. There are no automatic retries, which avoids blindly duplicating
notifications after partial or ambiguous delivery. The timeout applies to
blocking socket operations, not a total wall-clock deadline.

## Verification

Run from the repository root:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The development test suite requires Python 3.9 or newer. The helper uses
Python 3.6-compatible syntax and standard-library APIs; the tests also check
that syntax compatibility. Actual Python 3.6 execution is not tested.

Tests cover SMTP protocol exchanges with an in-memory socket substitute,
message contents, attachments, recipient rejection, TLS/authentication
behavior, configuration failures, and preservation of the original reporting
exception. Protocol tests use real `smtplib` serialization and SMTP commands;
TLS/authentication tests use mocks. No live email is sent by the tests.
