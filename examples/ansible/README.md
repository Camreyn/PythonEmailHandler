# Run the Python email tests from Ansible Automation Platform

AAP connects to your managed Linux servers, copies the required Python files,
and invokes Python there. Python imports `email_reporter` and sends the email.
No reporting task runs on localhost or delegates back to the AAP container.
Operator execution uses AAP Job Templates.

Use the [troubleshooting and test guide](../../docs/TROUBLESHOOTING.md) for
standalone Python tests, AAP setup, copying files, runtime checks, scenario
launches, expected output, and fixes.

## AAP Job Template catalog

Choose the synced PythonEmailHandler Project, your managed-server Inventory,
a Machine credential, and an approved execution environment. Use Job Type
**Run**:

| Playbook under `examples/ansible/` | Purpose |
| --- | --- |
| `deploy_and_test.yml` | Check target Python, copy helper/runner/example config, then run preflight and the selected tests |
| `preflight.yml` | Check copied files, interpreter/imports, and an INFO preview on each target |
| `test_email.yml` | Verify all cases, or select one with `email_case` |
| `info.yml` | INFO notification test |
| `forced_success.yml` | Forced success and SUCCESS notification test |
| `forced_failure.yml` | Forced failure and ERROR traceback notification test |
| `operation_timeout.yml` | Real child-process timeout and ERROR traceback notification test |
| `smtp_timeout.yml` | Injected SMTP timeout; verify delivery-error handling without contacting SMTP |
| `workflow_result.yml` | Preserve the operation's success/failure as the AAP job status |

The default inventory group is `email_test_targets`. Add your managed servers
to that group in AAP, or provide an existing host/group using `email_test_hosts`.
Paste these starting values into the Job Template's Extra Variables:

```yaml
email_test_hosts: email_test_targets
email_case: all
email_dry_run: true
email_python: /usr/bin/python3
email_repo_dir: /var/tmp/python-email-handler
email_operation_timeout_seconds: 2
```

Configure a valid target `ansible_python_interpreter` when required. Its
version must satisfy your EE's Ansible module requirements. `email_python`
is the reporting interpreter on the managed server, rather than a path inside
AAP's execution environment.

## Required files on managed servers

Deploy and Test copies three files from the synced AAP Project:

- Root `email_reporter.py`.
- `examples/ansible/email_scenarios.py`, preserving that relative layout.
- Root `email.example.ini` for previews.

Python must already be installed. The remote job account needs permission to
manage/read the destination. If your existing playbook copies the files, keep
the same layout, set `email_repo_dir` to that destination, and use Preflight
followed by Scenarios instead.

The deployment does not overwrite an actual `email.ini`. Provision real SMTP
configuration on the targets through your existing protected configuration
process, then supply its remote path as `email_config_file`.

Previews use the actual library with SMTP mocked. They validate an explicit
config and any required password environment variable, but do not test server
connectivity, login, or delivery. For a live test, set Extra Variables:

```yaml
email_case: info
email_dry_run: false
email_config_file: /etc/was-automation/email.ini
```

The full live suite attempts four emails per managed server. The SMTP timeout
is always injected and never sends mail. A required password environment
variable must be passed to the remote task, for example through
`email_environment` using protected AAP/Vault variables; a container-only
value is not automatically inherited remotely.

## Verify outcomes in AAP Job Output

| Case | Python exit | Notification | Scenarios job result |
| --- | --- | --- | --- |
| `info` | 0 | INFO | Successful |
| `success` | 0 | SUCCESS | Successful |
| `failure` | 1 | ERROR with forced RuntimeError traceback | Successful test when the expected report is sent/previewed |
| `timeout` | 124 | ERROR with TimeoutExpired traceback | Successful test when the expected report is sent/previewed |
| `smtp_timeout` | 2 | Deliberate delivery failure; `forced_smtp_timeout: true` | Successful test when the injected error is confirmed |

Inspect the JSON and assertion tasks. Previews report `notification_status:
preview`; SMTP acceptance reports `smtp_accepted` and a message identifier.
Confirm the managed hostname in the preview/email body and verify inbox
receipt for live sends. An unexpected config, attachment, or delivery failure
fails the verification test.

Use `workflow_result.yml` with `email_case: success`, `failure`, or `timeout`
when the operation must determine AAP's Successful/Failed job status. It
reports the notification before returning the operation result.

## Variables

| Variable | Default | Meaning on the managed server |
| --- | --- | --- |
| `email_test_hosts` | `email_test_targets` | Inventory host/group to target |
| `email_python` | `/usr/bin/python3` | Reporting interpreter path |
| `email_repo_dir` | `/var/tmp/python-email-handler` | Directory containing the copied files |
| `email_case` | `all` in the suite; `success` in Workflow Result | Scenario selection |
| `email_dry_run` | `true` | Mock SMTP while calling the actual Python library |
| `email_operation_timeout_seconds` | `0.2` | Child deadline, greater than 0 and at most 30 seconds |
| `email_config_file` | unset | Destination's example config for previews or `email.ini` for live sends |
| `email_context` | demonstration text | Cell, profile, JVM, environment, or change details |
| `email_environment` | empty mapping | Environment for the remote Python process |
| `email_recipients` | config recipients | List override in the suite/wrappers |
| `email_attachments` | no attachments | List of readable target files in the suite/wrappers |

Use `settings.example.yml` as a source of Extra Variables to paste into AAP.
`inventory.example.ini` illustrates managed-server settings; configure your
real inventory in AAP. These POSIX playbooks do not change WAS/BAW services.
Check job type skips Python runtime tests; use Run with previews instead.

Maintainer CI uses a separate synthetic inventory under `tests/fixtures/`;
that fixture is not the AAP deployment procedure.
