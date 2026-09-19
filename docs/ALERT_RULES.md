# LogShed Alert Rules Guide

This guide explains how to configure real-time alert rules in LogShed, details the difference between rule types, and provides practical examples for common monitoring scenarios.

---

## Overview

The LogShed Alert Engine evaluates incoming syslog and container log batches in memory as logs arrive. When log events match your rule criteria and cross configured thresholds, LogShed records an incident in the audit history and dispatches notifications via your configured notification targets (such as Pushover, Discord, Gotify, Telegram, Ntfy, or custom webhooks).

---

## Form Fields & Configuration

When creating or editing an alert rule, you configure the following fields:

### 1. Rule Name
A clear, descriptive name identifying the alert (for example, `SSH Auth Failure Spike` or `Kernel Panic Detected`). This name appears in notification titles and incident logs.

### 2. Rule Type
LogShed provides two distinct rule evaluation types:

- **Threshold (Sliding Window)**:
  - Tracks matching events within a rolling time window (for example, at least 5 events within 60 seconds).
  - Designed for burst detection, repeated failures, denial of service attempts, and rate anomalies.
  - Keeps an in-memory chronological sliding window for evaluation.
  - Displays linked **Threshold Count** and **Window Duration** options directly below the Rule Type selector.

- **Pattern (Immediate Match)**:
  - Triggers immediately upon a single occurrence of a matching log line (threshold of 1).
  - Designed for high-severity, critical alarms where even a single event demands immediate attention (for example, kernel panics, system halts, or unauthorized root logins).

### 3. Target Channel
Select a specific notification target or choose **All Enabled Channels** to broadcast alerts across all active webhooks and endpoints configured in Settings.

### 4. App Filter (Optional)
A multi-select dropdown listing known applications and daemons discovered across your ingested logs.
- You can select one or more specific apps (for example, `sshd`, `nginx`, `docker`).
- You can also enter custom expressions if needed.
- If left empty, all ingested logs from any application or service are evaluated.

### 5. Max Severity Filter (Optional)
Restricts rule evaluation to logs at or above a specific syslog severity level (syslog levels 0 to 7):
- `0 - Emergency`: System unusable
- `1 - Alert`: Action must be taken immediately
- `2 - Critical`: Critical conditions
- `3 - Error`: Error conditions
- `4 - Warning`: Warning conditions
- `5 - Notice`: Normal but significant condition
- `6 - Info`: Informational messages
- `7 - Debug`: Debug-level messages

Choosing `3 - Error` matches Emergency (0), Alert (1), Critical (2), and Error (3). If left empty, all severity levels are permitted.

### 6. Match Pattern (Regex or Substring)
A case-insensitive keyword substring or regular expression pattern matched against the log message text and raw payload.
- Case-insensitive substring matching: `failed password`
- Regular expression matching: `Failed (password|publickey) for .* from (?P<ip>\d+\.\d+\.\d+\.\d+)`
- Leave empty or enter `*` to match all log entries satisfying the app and severity filters.

### 7. Threshold Count & Window Duration (seconds)
*Appears when Rule Type is set to Threshold.*
- **Threshold Count**: Minimum number of matching events required to fire an alert (for example, `5`).
- **Window Duration**: Duration in seconds for the sliding time window (for example, `60` seconds).

### 8. Cooldown Flap Dampening (seconds)
Defines how long LogShed must suppress duplicate notifications after an alert fires.
- Prevents alert fatigue and notification flood during ongoing incidents.
- For example, a cooldown of `300` seconds ensures you receive one notification immediately when the incident begins, without receiving hundreds of repeat messages while the issue continues.

### 9. AI Root-Cause Incident Enrichment
When enabled, LogShed automatically redacts sensitive data (passwords, tokens, API keys, IPs) from triggering logs and requests root-cause diagnosis from your configured Large Language Model (Gemini, OpenAI, or local Ollama).
- Appends diagnosis summary and root cause insights to the incident record.
- In notification messages, a concise summary is included along with a link to review the full remediation steps in the LogShed UI.
- If AI providers encounter transient timeouts, LogShed automatically attempts configured fallback models before dispatching the alert.

---

## Practical Rule Examples

### Example 1: SSH Brute Force Infiltration Spike
- **Rule Type**: `Threshold (Sliding Window)`
- **Threshold Count**: `5`
- **Window Duration**: `60` seconds
- **App Filter**: `sshd`
- **Match Pattern**: `Failed password|authentication failure`
- **Cooldown**: `300` seconds
- **AI Enrichment**: Enabled
- **Use Case**: Detects credential stuffing or brute force SSH password attacks from external IP addresses.

### Example 2: Web Server 5xx Outage Burst
- **Rule Type**: `Threshold (Sliding Window)`
- **Threshold Count**: `10`
- **Window Duration**: `30` seconds
- **App Filter**: `nginx`, `caddy`, `traefik`
- **Match Pattern**: `HTTP/[12]\.[01]" 50[0-9]`
- **Cooldown**: `180` seconds
- **AI Enrichment**: Enabled
- **Use Case**: Alerts when backend microservices or database connections begin failing and serving 500-series server errors to clients.

### Example 3: Kernel Panic or Hardware Failure
- **Rule Type**: `Pattern (Immediate Match)`
- **Max Severity Filter**: `2 - Critical`
- **Match Pattern**: `Kernel panic|Out of memory|Hardware Error|machine check`
- **Cooldown**: `60` seconds
- **AI Enrichment**: Enabled
- **Use Case**: Instantly notifies systems administrators of operating system kernel crashes, hardware faults, or out-of-memory kernel kills.

### Example 4: Sudo Elevation Alarm
- **Rule Type**: `Pattern (Immediate Match)`
- **App Filter**: `sudo`
- **Match Pattern**: `COMMAND=/bin/su|NOT in sudoers`
- **Cooldown**: `30` seconds
- **AI Enrichment**: Disabled
- **Use Case**: Security audit alarm for unexpected root escalation attempts or unauthorized sudo invocations.

---

## Testing Rules

Use the **Test Rule** button on any configured rule to open the dry-run tester. Enter sample log messages to verify that your regular expression or substring matches as expected and confirm that offending IP addresses are parsed correctly before activating rules in production.
