# Trigger email and timeout notification tests from AAP

Use **`timeout`** for a test that times out an operation and then sends an ERROR
email. **`smtp_timeout`** simulates a broken SMTP connection and deliberately
sends nothing.

AAP launches the Python script **on your managed servers**. The email code
does not run on localhost or inside the AAP container.

## 1. Make sure the required files exist on each target

Using the example destination, keep this layout:

| File | Target location |
| --- | --- |
| Email library | `/var/tmp/python-email-handler/email_reporter.py` |
| Test runner | `/var/tmp/python-email-handler/examples/ansible/email_scenarios.py` |
| Your SMTP configuration | `/etc/was-automation/email.ini` |

Set the paths below to match where your existing playbook copies them. Python
must be installed, and the remote execution account must be able to read these
files. `email_python` selects the reporting interpreter on the target. The
target's Ansible module interpreter must also meet your execution environment's
Ansible requirements; configure `ansible_python_interpreter` when necessary.

## 2. Configure an AAP Job Template

Select your managed-server Inventory, Machine credential, and
PythonEmailHandler Project. Set **Job Type: Run**.

Choose one of these playbooks:

| Playbook | When to use it |
| --- | --- |
| `examples/ansible/deploy_and_test.yml` | Copy the Python files, verify them, and run tests |
| `examples/ansible/test_email.yml` | Files are already copied; run tests only |

The deployment playbook copies the helper, runner, and example config. Your
actual SMTP config must already be provisioned on the managed server; the
deployment does not overwrite it.

## 3. Test an informational email

Enter these **Extra Variables** in the Job Template, replacing the inventory
group and paths:

```yaml
email_test_hosts: your_inventory_group
email_python: /usr/bin/python3
email_repo_dir: /var/tmp/python-email-handler
email_config_file: /etc/was-automation/email.ini

email_case: info
email_dry_run: false

email_context: |
  Environment: TEST
  Profile: AppSrv01
  JVM: server1
```

Launch the job. `email_dry_run: false` sends an actual email. Use `true` to
preview it without connecting to SMTP. An explicit config must exist and be
readable even during a preview.

## 4. Test the timeout email

Keep the same settings and change:

```yaml
email_case: timeout
email_operation_timeout_seconds: 2
email_dry_run: false
```

Launch again. Python starts a harmless sleeping subprocess, stops it after
two seconds, catches the timeout, and sends an ERROR email containing the
traceback. It does not stop or alter WAS/BAW services.

The reporting Python stays alive to send this notification. This does not
simulate cancelling an AAP job or killing the reporting process itself.

## 5. Add the Python trigger to your own playbook

These tasks can be placed under `tasks:` in an existing play that targets your
managed servers. Define `email_python`, `email_repo_dir`, and
`email_config_file` as shown above. This task example performs a **live send**;
it does not include the runner's `--dry-run` argument.

```yaml
- name: Trigger the Python timeout notification test on the managed server
  ansible.builtin.command:
    argv:
      - "{{ email_python }}"
      - "{{ email_repo_dir }}/examples/ansible/email_scenarios.py"
      - --scenario
      - timeout
      - --timeout-seconds
      - "2"
      - --config-file
      - "{{ email_config_file }}"
  environment: "{{ email_environment | default({}) }}"
  register: timeout_test
  changed_when: timeout_test.rc == 124
  failed_when: timeout_test.rc != 124

- name: Display the Python notification result
  ansible.builtin.debug:
    msg: "{{ timeout_test.stdout | from_json }}"
```

For an INFO test, change the argument following `--scenario` to `info` and
expect return code `0` instead of `124` in both conditions. The existing
`test_email.yml` already handles these cases and their expected return codes.

If your INI uses a password environment variable, pass its protected value to
the remote task using `email_environment`. A value available only inside the
AAP container is not automatically inherited by a managed server.

## 6. Verify AAP Job Output and the actual mailbox

| Test | Expected Python return | Expected JSON | Expected email |
| --- | --- | --- | --- |
| `info` | `0` | `notification_status: smtp_accepted` | INFO report |
| `timeout` | `124` | `operation_status: timeout`, `notification_status: smtp_accepted` | ERROR report with a `TimeoutExpired` traceback |

The verification playbook should finish **Successful** when the timeout
behaves as expected. A delivery failure returns `2` and fails the test. For
previews, expect `notification_status: preview` and no received email.

For live tests, confirm the received subject/severity, managed hostname,
timestamp, and traceback. Record the `message_id` from Python's JSON if mail
administrators need to trace delivery. SMTP acceptance alone does not confirm
inbox receipt.

If you also want the timeout to produce a **Failed AAP job** for workflow
branching, select `examples/ansible/workflow_result.yml` instead, with
`email_case: timeout` and the same target/config settings.

For every scenario, runtime preflight checks, and common errors, see the
[full troubleshooting guide](TROUBLESHOOTING.md).
