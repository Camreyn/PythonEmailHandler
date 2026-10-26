# Test the Python email reporter from Ansible

Ansible launches `email_scenarios.py` with `ansible.builtin.command` and an
argument list. Python imports `email_reporter` and calls `send_email()`.
Ansible never sends the email itself. These examples do not start, stop, or
change WAS ND or BAW services.

## Run all cases without sending email

From the repository root on a Linux Ansible controller or in an AAP execution
environment:

```sh
ansible-playbook -i examples/ansible/inventory.example.ini examples/ansible/test_email.yml
```

The playbook defaults to `email_dry_run=true`. Python uses the example config
and the real email library, with only its SMTP transport mocked. Its JSON
output includes the assembled subject, body, recipient list, and attachment
names. No SMTP connection is made and no credentials are required for the
default example config.

Use your existing compatible Ansible installation. To reproduce the pinned CI
environment with Python 3.12 or newer on Linux:

```sh
python -m pip install -r requirements-dev.txt -r examples/ansible/requirements-test.txt
```

The Python reporter and runner themselves have no third-party dependencies.

## Individual cases

Each entry point defaults to a preview. `test_email.yml -e email_case=CASE`
selects the same case without using its wrapper playbook.

| Playbook in `examples/ansible/` | Case | Python result | Notification |
| --- | --- | --- | --- |
| `info.yml` | `info` | Exit 0 | INFO report |
| `forced_success.yml` | `success` | Exit 0 | SUCCESS report |
| `forced_failure.yml` | `failure` | Exit 1 | ERROR report with a forced exception traceback |
| `operation_timeout.yml` | `timeout` | Exit 124 | ERROR report after a child process exceeds its deadline |
| `smtp_timeout.yml` | `smtp_timeout` | Exit 2 | A forced SMTP connection timeout is caught; no email is sent |

Examples:

```sh
ansible-playbook -i examples/ansible/inventory.example.ini examples/ansible/info.yml
ansible-playbook -i examples/ansible/inventory.example.ini examples/ansible/forced_success.yml
ansible-playbook -i examples/ansible/inventory.example.ini examples/ansible/forced_failure.yml
ansible-playbook -i examples/ansible/inventory.example.ini examples/ansible/operation_timeout.yml -e email_operation_timeout_seconds=2
ansible-playbook -i examples/ansible/inventory.example.ini examples/ansible/smtp_timeout.yml
```

These are verification playbooks: the deliberate failure, timeout, and SMTP
error cases count as passing tests only when the expected Python exit code,
severity, operation status, and notification outcome match. For example, a
forced failure must return 1 **and** successfully send or preview an ERROR
notification. A real delivery problem returns 2 and fails that test. An invalid
config also fails the SMTP timeout test; it cannot masquerade as the intended
injected timeout.

## Send actual test emails

Copy `email.example.ini` to `email.ini` beside the root `email_reporter.py`,
then enter your SMTP server, sender, and recipients. For real delivery, disable
the preview explicitly:

```sh
ansible-playbook -i examples/ansible/inventory.example.ini examples/ansible/info.yml -e '{"email_dry_run": false}'
ansible-playbook -i examples/ansible/inventory.example.ini examples/ansible/test_email.yml -e '{"email_dry_run": false}'
```

The complete live suite attempts four emails per target: INFO, SUCCESS,
forced failure, and operation timeout. The SMTP timeout case always injects
an exception before contacting the server, including in live mode. It tests
delivery-error handling rather than your network's actual timeout behavior.
For an actual SMTP socket deadline, use `timeout_seconds` in your INI config
with the real SMTP server; unexpected delivery errors make the test fail.

Use a different config path when needed:

```sh
ansible-playbook -i examples/ansible/inventory.example.ini examples/ansible/info.yml \
  -e '{"email_dry_run": false, "email_config_file": "/etc/was-automation/email.ini"}'
```

The operation timeout is a real Python `subprocess.run(timeout=...)` deadline
on a harmless sleeping child. Python remains alive to catch the timeout,
terminate/reap the child, send the report, and return 124. An Ansible job kill,
unreachable server, or terminated reporting process cannot rely on that same
process to send a notification. No Ansible async kill is used in this example.

## Test AAP success and failure branches

`workflow_result.yml` preserves the operation's status as the Ansible job
status. It defaults to preview mode too:

```sh
# Successful Ansible/AAP job after a SUCCESS notification preview.
ansible-playbook -i examples/ansible/inventory.example.ini examples/ansible/workflow_result.yml -e email_case=success

# Failed Ansible/AAP job after an ERROR notification preview.
ansible-playbook -i examples/ansible/inventory.example.ini examples/ansible/workflow_result.yml -e email_case=failure

# Failed Ansible/AAP job after an operation-timeout ERROR notification preview.
ansible-playbook -i examples/ansible/inventory.example.ini examples/ansible/workflow_result.yml -e email_case=timeout
```

Add `-e '{"email_dry_run": false}'` to send the notification before returning
the job result. Ansible's exit code is its own job exit code; the driver's
0, 1, 124, or 2 appears in the command result and Python JSON.

For an AAP Job Template, select `examples/ansible/workflow_result.yml`, target
an appropriate inventory, and provide `email_case` and `email_dry_run` as extra
variables or survey inputs. Use `test_email.yml` instead when the job should
pass after verifying all expected error cases. On localhost the Python script
runs in the execution environment, which needs the repository checkout,
config, and SMTP access. For a managed host, use the paths and interpreter
available on that host as described below.

## Variables and additional report details

Use `-e @examples/ansible/settings.example.yml` as a starting point. Avoid
putting real credentials into that example file.

| Variable | Default | Purpose |
| --- | --- | --- |
| `email_case` | `all` in the test suite; `success` in the workflow example | Select the test case |
| `email_dry_run` | `true` | Mock SMTP while running the actual Python library |
| `email_operation_timeout_seconds` | `0.2` | Child-process deadline; greater than 0 and at most 30 seconds |
| `email_config_file` | unset | Python uses `email.example.ini` for previews or `email.ini` for live sends |
| `email_context` | demonstration text | Include cell, node, profile, JVM, environment, or change details |
| `email_python` | controller's Ansible Python interpreter | Interpreter on the execution host |
| `email_repo_dir` | repository root derived from the playbook path | Checkout on the execution host |
| `email_test_hosts` | `localhost` | Target host or inventory group |
| `email_environment` | empty mapping | Optional child-process environment, such as an injected SMTP password |
| `email_recipients` | config recipients | Override with a list in the test suite/wrapper playbooks |
| `email_attachments` | no attachments | Add a list of paths in the test suite/wrapper playbooks |

For example, create a local, uncommitted extra-vars file with:

```yaml
email_case: info
email_dry_run: true
email_context: |
  Environment: TEST
  Cell: TestCell
  Node: Node01
  Profile: AppSrv01
  JVM: server1
email_recipients:
  - was-test-reports@example.com
email_attachments:
  - /var/tmp/was-health-report.txt
```

Then run `test_email.yml -e @/path/to/your-vars.yml`. Attachment paths and the
config must exist on the execution host. A preview with an explicit config
also validates that config and any password environment variable it requires.
The JSON previews print report contents, so use suitable test logs and context.

If the INI uses `password_env = SMTP_PASSWORD`, make that variable available
to the Python process. For example, obtain `vault_smtp_password` from Ansible
Vault or an AAP credential injector and pass:

```yaml
email_environment:
  SMTP_PASSWORD: "{{ vault_smtp_password }}"
```

## Managed Linux servers

The default examples execute on localhost. To execute Python on a managed
server, place a checkout and configured INI on that server first, then edit
the commented `was_test_servers` example in `inventory.example.ini`. Set:

```ini
[was_test_servers]
was-test01 ansible_host=192.0.2.10 ansible_user=automation

[was_test_servers:vars]
email_repo_dir=/opt/PythonEmailHandler
email_python=/usr/bin/python3
email_config_file=/etc/was-automation/email.ini
```

Run with `-e email_test_hosts=was_test_servers`. The playbooks do not install
Python, copy credentials, or deploy the checkout. The reporting interpreter
must be Python 3, rather than WAS `wsadmin` Jython. The host also needs the
Python version required by the Ansible controller's modules. These playbooks
use POSIX `ansible.builtin.command`; native Windows targets are not included.

## Verification

`ansible-playbook ... --check` does not run the Python reporter or send mail.
Use `email_dry_run=true` to exercise the real cases without sending mail.

The repository's GitHub Actions workflow runs Python tests, Ansible syntax
checks, all the preview entry points, check mode, unexpected-delivery failure
checks, and expected failed AAP job examples on a Linux runner. It never
supplies live SMTP credentials or disables preview mode.

Relevant Ansible references:

- [Command module and argv](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/command_module.html)
- [Error handling and failed_when](https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_error_handling.html)
