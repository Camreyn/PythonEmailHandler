# Troubleshoot Python email reporting and AAP job tests

This guide has two independent paths:

1. [Test directly with Python](#1-test-directly-with-python) on the system that
   will run the reporting code.
2. [Test Ansible plus Python through AAP](#2-test-ansible-plus-python-through-aap)
   using Projects, Inventories, Job Templates, and Job Output in Ansible
   Automation Platform. There are no Ansible command-line steps for operators.

In both paths, **Python calls `email_reporter.send_email()`**. Ansible only
launches Python. The example operations are synthetic and do not change any
WAS ND or BAW services.

## 1. Test directly with Python

### Step 1: Check the interpreter and files

These standalone Python commands use a Linux example checkout at
`/opt/PythonEmailHandler`. Substitute your real checkout and interpreter paths:

```sh
cd /opt/PythonEmailHandler
python3 --version
python3 -c 'import sys; print(sys.executable); assert sys.version_info >= (3, 6), "Python 3.6+ is required"'
python3 -c 'import email_reporter; print(email_reporter.__file__)'
python3 examples/ansible/email_scenarios.py --help
```

Verify that:

- The interpreter is Python 3.6 or newer, rather than WAS `wsadmin` Jython.
- The import resolves to this checkout's `email_reporter.py`.
- The scenario runner displays its help without an import error.
- `email.example.ini` exists in the checkout root for preview tests.

The reporting module and scenario runner use only the Python standard library.
They do not require `pip install` for normal use. The separate development
test suite requires Python 3.9+ and the packages in `requirements-dev.txt`.

### Step 2: Preview each scenario without sending email

Run these from the repository root:

```sh
python3 examples/ansible/email_scenarios.py --scenario info --dry-run
python3 examples/ansible/email_scenarios.py --scenario success --dry-run
python3 examples/ansible/email_scenarios.py --scenario failure --dry-run
python3 examples/ansible/email_scenarios.py --scenario timeout --timeout-seconds 2 --dry-run
python3 examples/ansible/email_scenarios.py --scenario smtp_timeout --dry-run
```

Read the JSON printed by each command. On a Linux shell, run `echo $?`
immediately after a command to inspect its process exit code. In PowerShell,
use `$LASTEXITCODE` instead and select the available Python executable.

| Scenario | Expected Python exit | `operation_status` | `level` | Preview verification |
| --- | --- | --- | --- | --- |
| `info` | 0 | `info` | `INFO` | `notification_status: preview`; informational subject/body |
| `success` | 0 | `success` | `SUCCESS` | `notification_status: preview`; success subject/body |
| `failure` | 1 | `failure` | `ERROR` | `notification_status: preview`; traceback contains the forced `RuntimeError` |
| `timeout` | 124 | `timeout` | `ERROR` | `notification_status: preview`; traceback contains `TimeoutExpired` |
| `smtp_timeout` | 2 | `info` | `INFO` | `notification_status: failed`; `forced_smtp_timeout: true` |

For the first four cases, the JSON `preview` contains the assembled email
subject, body, recipients, and attachment names. The body includes the hostname,
UTC timestamp, and level. The library still builds the real message; the SMTP
transport is mocked. A preview-generated `message_id` is not evidence that
anything reached an SMTP server.

The operation timeout uses a real deadline on a harmless sleeping child.
Python kills and reaps the child, catches the timeout, and builds the ERROR
notification. The default deadline is 0.2 seconds; accepted values are greater
than 0 and at most 30 seconds.

The SMTP timeout is a deliberately injected connection exception. It never
contacts your SMTP server, even if `--dry-run` is omitted. It verifies the
delivery-error path, not your network's actual timeout behavior. A missing
config also returns 2, but has `forced_smtp_timeout: false` and is not a passing
SMTP timeout test.

### Step 3: Check the actual configuration in preview mode

Copy `email.example.ini` to a config location accessible to the reporting
account, such as `/etc/was-automation/email.ini`. For an unauthenticated
internal relay, the relevant settings might be:

```ini
[smtp]
host = smtp.internal.example.com
port = 25
security = none
from_address = was-automation@example.com
to = was-test-reports@example.com
subject_prefix = WAS/BAW TEST
timeout_seconds = 15
```

Use your SMTP server's actual TLS, port, sender, recipient, and authentication
settings. With authentication, configure `username` plus either `password` or
`password_env`. A configured `password_env` must exist in the Python process's
environment and takes precedence over an inline password. For a private CA,
use `ca_file`; its relative path resolves beside the INI file.

Preview with that exact config:

```sh
python3 examples/ansible/email_scenarios.py --scenario info --dry-run --config-file /etc/was-automation/email.ini
```

Success verifies config loading, required password environment variables,
message construction, and attachment readability if you supplied attachments.
It does **not** verify SMTP reachability, a server's certificate, login, or
delivery, because the SMTP connection is mocked.

If `--config-file` is omitted, the scenario runner uses root `email.example.ini`
for previews and root `email.ini` for live sends. An explicitly supplied config
must exist even in preview mode. Relative explicit config paths resolve from
the process's working directory; prefer absolute paths in jobs.

### Step 4: Send and verify real test messages

Remove `--dry-run` and use the configured INI:

```sh
python3 examples/ansible/email_scenarios.py --scenario info --config-file /etc/was-automation/email.ini
python3 examples/ansible/email_scenarios.py --scenario success --config-file /etc/was-automation/email.ini
python3 examples/ansible/email_scenarios.py --scenario failure --config-file /etc/was-automation/email.ini
python3 examples/ansible/email_scenarios.py --scenario timeout --timeout-seconds 2 --config-file /etc/was-automation/email.ini
```

Each command attempts one message. The success and info cases return 0 after
SMTP acceptance; the failure and operation timeout cases still return 1 and
124 after successfully sending their corresponding ERROR notification. A
delivery problem instead returns 2 while retaining `operation_status` and
`operation_exit_code` for the original operation.

Verify all of the following:

1. JSON contains `notification_status: smtp_accepted` and a `message_id`.
2. The intended recipient receives the matching subject and severity.
3. Hostname and timestamp identify the system and run you just tested.
4. Failure and timeout messages contain the appropriate traceback.
5. If acceptance is reported but nothing arrives, use the message identifier,
   sender, recipient, and time with mail administrators to trace the relay or
   downstream delivery. Check mailbox quarantine/spam as well. SMTP acceptance
   alone does not confirm inbox delivery.

For an actual SMTP timeout, set the INI's `timeout_seconds` to the required
socket deadline and test your real server. The value applies to blocking
socket operations, not a total process deadline. Record the underlying error
type reported by Python; do not confuse this with the injected `smtp_timeout`
scenario.

### Step 5: Exercise the library directly from another Python script

Place this script beside `email_reporter.py`, or make the helper importable
through your application's normal module layout. This example sends a real
INFO message using the specified config:

```python
import logging

from email_reporter import EmailError, send_email

logging.basicConfig(level=logging.INFO)

try:
    message_id = send_email(
        subject="Direct Python email verification",
        body="Cell: TestCell\nProfile: AppSrv01\nJVM: server1\nTest: INFO notification",
        level="INFO",
        config_file="/etc/was-automation/email.ini",
    )
except EmailError:
    logging.exception("Email verification failed")
    raise
else:
    print("SMTP accepted the message:", message_id)
```

For an existing exception handler, pass the caught exception with
`exception=error`. The [main usage guide](../README.md) demonstrates preserving
the original operation failure if its email notification also fails.

Optional standalone regression tests, with development dependencies already
installed, are `python3 -m pytest -q` from the repository root. The tests do not
send live email. Python 3.6 syntax compatibility is checked, but execution on
an actual Python 3.6 interpreter is not covered by this repository's CI.

## 2. Test Ansible plus Python through AAP

Your operating model is **AAP → managed server → Python → SMTP**. AAP copies the required Python files to the servers it connects to and invokes Python there. All operator launches below use AAP Job Templates. No reporting task runs on localhost or delegates back to the AAP container.

### Step 1: Check what must exist on each managed server

| Location | Required content | What runs there |
| --- | --- | --- |
| AAP Project / execution environment | Synced source files and Ansible's normal runtime | Ansible reads the project files and dispatches copy/command tasks |
| Each managed reporting server | Python 3, copied helper and runner, readable SMTP config, and any attachments | Python imports the helper and sends the email |

The reporting interpreter and files must exist on **each managed server that executes the task**. An AAP Project sync does not automatically copy project content onto managed servers. The deployment playbook below performs that copy; running the helper in Red Hat's AAP container is not part of this setup.

`email_python` selects the target reporting interpreter. `ansible_python_interpreter` selects the target interpreter for Python-based Ansible modules. Both must be valid; the latter must satisfy the target-Python requirements of the Ansible version in your execution environment. The reporter's own Python 3.6 minimum does not lower those requirements.

The defaults are `/usr/bin/python3` and `/var/tmp/python-email-handler` on the managed servers. Choose different paths if your standards require them. The remote job account must be able to create/read the destination and read the config. Use AAP privilege escalation when required, and keep ownership/permissions compatible with the account used by later reporting jobs.

### Step 2: Configure the AAP Project and managed inventory

1. Create or update the AAP **Project** for `https://github.com/Camreyn/PythonEmailHandler`, branch `main`.
2. Synchronize the Project and verify the update succeeded.
3. Select an Inventory containing the managed Linux servers. Create/use a group called `email_test_targets`, or set `email_test_hosts` to your existing host/group.
4. Select the Machine credential for those servers and your approved execution environment.

Example inventory/group variables entered in AAP:

```yaml
email_python: /usr/bin/python3
ansible_python_interpreter: /usr/bin/python3
email_repo_dir: /var/tmp/python-email-handler
```

Replace the interpreter paths with the installed Python appropriate for each server and your Ansible version. A Python path from the AAP container may not exist remotely. Do not set `ansible_connection: local` for these managed-server tests.

### Step 3: Copy the files and run the tests from AAP

Create a Job Template with:

| Field | Setting |
| --- | --- |
| Name | `EmailReporter - Deploy and Test` |
| Job Type | **Run** |
| Project | The synced PythonEmailHandler project |
| Playbook | `examples/ansible/deploy_and_test.yml` |
| Inventory | Your managed-server inventory |
| Execution environment | Your approved execution environment |
| Credentials | Appropriate Machine credential and any protected SMTP variable injection |

Paste these starting Extra Variables into the template, then select **Launch**:

```yaml
email_test_hosts: email_test_targets
email_case: all
email_dry_run: true
email_operation_timeout_seconds: 2
email_context: |
  Environment: TEST
  Stack: WAS ND / BAW
  Cell: TestCell
  Profile: AppSrv01
  JVM: server1
```

The job performs three stages on every selected server:

1. **Verify and copy.** A `raw` task invokes target Python and checks its version and SSL support before normal Python-based Ansible modules run. The job creates the destination layout and copies the following files from the AAP Project.

   | Project source | Target path relative to `email_repo_dir` |
   | --- | --- |
   | `email_reporter.py` | `email_reporter.py` |
   | `examples/ansible/email_scenarios.py` | `examples/ansible/email_scenarios.py` |
   | `email.example.ini` | `email.example.ini` |

2. **Preflight.** Verify the copied runner and helper import, print the actual target hostname/interpreter/module paths, and produce an INFO preview.
3. **Scenario verification.** Run the requested cases through target Python and check their exit codes, severity, and notification outcome.

This job copies the sample INI for previews. It does not install Python, copy a real SMTP password from Git, or overwrite a separately configured `email.ini`. Provision the real config through your existing protected configuration process on the managed servers.

The files need read permission rather than executable permission because Ansible invokes the Python interpreter with the script path. Deployment uses private directory/file permissions. An older deployment owned by another account may need its target permissions corrected before a new job can use it.

Expected result: **Successful** job, no failed/unreachable hosts, and all scenario assertions passing. In **Display the interpreter and helper actually used on this host**, confirm the hostname is the intended managed server, `helper_import` is `ok`, and the paths point to your target destination.

### Step 4: Verify files copied by your existing playbook

If your existing process already copies the files, skip Deploy and Test. Preserve the relative layout shown above and create `EmailReporter - Preflight`, selecting `examples/ansible/preflight.yml`. Use the same Inventory, Machine credential, execution environment, and remote interpreter settings.

Example Extra Variables:

```yaml
email_test_hosts: email_test_targets
email_repo_dir: /opt/PythonEmailHandler
email_python: /usr/bin/python3
email_config_file: /etc/was-automation/email.ini
```

Use paths that exist on those managed servers. Launch the template with Job Type **Run**. Preflight always performs an INFO preview; it never sends email, regardless of `email_dry_run`.

It verifies the target interpreter, Python 3.6+, runner path, helper import and imported module location, OpenSSL availability, normal Ansible command-module execution, and config/message construction. It prints the target hostname, Python executable/version, repository root, helper path, and runner path.

The initial `raw` probe does not itself require remote Python for an Ansible module. If that probe passes but the following command task fails, check `ansible_python_interpreter` and whether that interpreter satisfies your EE's Ansible target requirements.

Expected output is `helper_import: ok` plus INFO JSON with `notification_status: preview` and `exit_code: 0`. An explicitly supplied config must exist and its required password environment variable must be available. A preview does not prove SMTP connectivity, certificate validity at the server, login, or delivery. A **Check** job skips runtime probes; use **Run** for verification.

### Step 5: Launch and verify each scenario from AAP

After deployment, create `EmailReporter - Scenarios`, selecting `examples/ansible/test_email.yml`. Reuse the same managed Inventory, Machine credential, EE, and remote path variables. Start with `email_dry_run: true`.

Change `email_case` in Extra Variables and launch separate tests:

| Extra Variable | What target Python tests | Expected AAP verification job result |
| --- | --- | --- |
| `email_case: info` | INFO notification | Successful; Python exit 0 |
| `email_case: success` | Forced success and SUCCESS notification | Successful; Python exit 0 |
| `email_case: failure` | Forced failure and ERROR traceback notification | Successful **test**; Python exit 1 expected |
| `email_case: timeout` | Real child deadline and ERROR traceback notification | Successful **test**; Python exit 124 expected |
| `email_case: smtp_timeout` | Injected SMTP connection timeout | Successful **test**; Python exit 2 and `forced_smtp_timeout: true` expected |
| `email_case: all` | All five cases | Successful only when every case matches its expected result |

Alternatively, create individual AAP Job Templates selecting `info.yml`, `forced_success.yml`, `forced_failure.yml`, `operation_timeout.yml`, or `smtp_timeout.yml` under `examples/ansible/`. They select the case for you; the target files still need to be copied first.

In AAP Job Output, expand these tasks:

- **Invoke Python for each selected case:** inspect the target process `rc` and JSON stdout.
- **Verify each expected Python exit code:** every assertion must pass.
- **Verify report severity and notification outcome:** every assertion must pass.
- **Display the reports produced by Python:** compare the JSON with the direct-Python scenario table and confirm the managed hostname in preview bodies.

The suite explicitly checks deliberate nonzero returns. An unexpected delivery failure during the operation-timeout case returns 2 instead of 124 and fails that test. A bad config cannot pass as an injected SMTP timeout because `forced_smtp_timeout` must also be true.

### Step 6: Send and verify actual emails from the managed servers

Make the real SMTP INI readable by the remote job account on each server. The managed server must be able to reach the SMTP server. Start with one live INFO case in the Scenarios template:

```yaml
email_test_hosts: email_test_targets
email_case: info
email_dry_run: false
email_repo_dir: /var/tmp/python-email-handler
email_python: /usr/bin/python3
email_config_file: /etc/was-automation/email.ini
```

Launch in AAP. Verify `notification_status: smtp_accepted` and a `message_id`, then verify receipt, hostname, timestamp, and subject in the actual mailbox. Repeat with `success`, `failure`, and `timeout`. `all` attempts four emails **per managed server**. The `smtp_timeout` case is always injected and never sends a message.

If the INI uses `password_env = SMTP_PASSWORD`, supply that variable to the **remote Python task**. A variable in a personal terminal or only in the AAP container does not automatically become a remote process environment variable. Obtain a protected value through Ansible Vault or an AAP credential injector, for example:

```yaml
email_environment:
  SMTP_PASSWORD: "{{ vault_smtp_password }}"
```

This mapping supplies the target task environment; it does not put a literal password into a process argument or committed example. Preflight can validate the same target config and environment without sending mail.

Optional suite/wrapper variables:

```yaml
email_recipients:
  - was-test-reports@example.com
email_attachments:
  - /var/tmp/was-health-report.txt
```

Both values must be lists. Attachments must be readable on the managed server. Preflight and Workflow Result do not consume these two overrides. Previews print report contents, so use suitable test logs/context.

### Step 7: Verify AAP success and failure workflow branches

Create `EmailReporter - Workflow Result`, selecting `examples/ansible/workflow_result.yml`, with the same managed target settings. With the files deployed, launch separate jobs using:

```yaml
email_test_hosts: email_test_targets
email_case: success
email_dry_run: true
```

Then change `email_case` to `failure`, and then to `timeout`:

| Case | Target Python action before returning | Expected AAP job status |
| --- | --- | --- |
| `success` | SUCCESS preview or live notification, exit 0 | Successful |
| `failure` | ERROR preview or live notification, exit 1 | Failed |
| `timeout` | ERROR preview or live notification, exit 124 | Failed |
| Any unexpected notification failure | Delivery error, exit 2 | Failed |

For real sends, set `email_dry_run: false` and the target config path. Even for a Failed command task, stdout contains the Python JSON. Confirm `preview` or `smtp_accepted` before judging the expected operation failure. Python's process return code is not the AAP job's own exit code.

Connect **On Success** and **On Failure** branches to this job template in an AAP Workflow Job Template. Scenarios passes when expected errors behave correctly; Workflow Result retains a Failed job status for the original operation failure.

Cancelling an AAP job, losing the server connection, or killing the reporting process differs from the caught child timeout. A terminated reporter cannot reliably send a final message. The example keeps the reporting Python alive to send the timeout ERROR notification.

## 3. Symptoms, checks, and fixes

| Symptom | What to check | What to do |
| --- | --- | --- |
| No hosts matched / task never runs | AAP Inventory, `email_test_hosts`, job Limit, and disabled hosts | Use a host/group present in that job's inventory and a matching Limit |
| Preflight says interpreter not found | `email_python` on the actual execution host | Select an installed Python 3 path there; installing Python on the AAP host does not install it on the managed server |
| Module execution fails although the raw interpreter probe passes | `ansible_python_interpreter`, discovery output, and the EE's Ansible target requirements | Set a valid supported module interpreter; it may differ from the reporter interpreter |
| `ModuleNotFoundError: email_reporter` | Repository root and helper path printed by preflight | Run Deploy and Test, or recopy the files to the managed server and correct `email_repo_dir` |
| Python cannot open `email_scenarios.py` | Runner path, permissions, and project revision | Correct the remote checkout/path or update the Project; a Project sync alone does not deploy remote files |
| Cannot read SMTP configuration | The config path and permissions on the managed server | Make the INI available to the job account on that host; use an absolute `email_config_file` |
| Password environment variable missing | INI `password_env` and the Python task environment | Supply the required variable through the AAP job/remote task environment; an inline password does not override a named missing environment variable |
| `notification_status: preview` but no email arrives | `email_dry_run` or the runner's `--dry-run` | This is expected for previews; switch the AAP Extra Variable to native YAML `false` for live tests |
| Connection refused / DNS failure / socket timeout | `delivery_error_type`, SMTP host/port, and connectivity from the execution host | Check the relay listener, routing/firewall/DNS, and INI port/security settings from that context |
| `SSLCertVerificationError` | Certificate hostname and trusted CA on the managed server | Use the correct SMTP hostname and configured CA bundle; do not disable certificate checks |
| `SMTPNotSupportedError` | Whether the configured server/port supports STARTTLS or other requested capabilities | Match INI `security` and port to the server's actual service |
| `SMTPAuthenticationError` | Username, credential source, and permitted SMTP authentication | Correct the credential/injection and verify that the account may authenticate to that relay |
| Sender or recipient rejection | SMTP error response and relay policies | Verify the sender, recipients, and relay permissions; partial acceptance can mean some recipients already received the message |
| Cannot read attachment | File location/readability on the actual execution host | Use a readable path there; a file on your desktop or AAP container may not exist on the managed server |
| Job succeeds for a forced failure | Selected playbook and Python JSON | Expected for `test_email.yml`; use `workflow_result.yml` to retain Failed job status |
| Workflow Result fails after reporting success | `operation_status` versus `notification_status`, and driver exit code | Read both fields: a successful operation can still have a failed notification, and vice versa |
| INI or CA file exists on AAP but not the reporting server | Target filesystem and permissions | Place the required file on every managed reporting server, then rerun preflight |
| Successful SMTP acceptance but no inbox message | `message_id`, recipient, time, relay logs, quarantine/spam | Trace downstream delivery; do not treat SMTP acceptance as an inbox receipt |

Keep the AAP job ID, project revision, execution environment, inventory target,
preflight runtime paths, scenario, Python exit code, notification status,
message identifier, and relevant error type when comparing runs. Do not put
SMTP passwords into screenshots, job notes, or committed examples.

## References

- [Red Hat: execution environments](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.5/html/using_automation_execution/assembly-controller-execution-environments)
- [Red Hat: job templates](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.5/html/using_automation_execution/controller-job-templates)
- [Ansible: interpreter discovery](https://docs.ansible.com/projects/ansible/latest/reference_appendices/interpreter_discovery.html)
- [Ansible: raw module](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/raw_module.html)
