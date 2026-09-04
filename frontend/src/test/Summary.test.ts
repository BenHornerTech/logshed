import { describe, it, expect } from 'vitest';
import { extractCleanSummary } from '../utils/summary.ts';

describe('extractCleanSummary Utility', () => {
  it('extracts ## Summary section and strips markdown backticks, bold, headers', () => {
    const rawResponse = `## Summary
The \`technitium-sync\` service completed its synchronization **successfully**.

## Root Cause
No issues found.

## Actionable Remediation
Continue monitoring.`;

    const summary = extractCleanSummary(rawResponse);
    expect(summary).toBe('The technitium-sync service completed its synchronization successfully.');
    expect(summary).not.toContain('## Summary');
    expect(summary).not.toContain('`');
    expect(summary).not.toContain('**');
  });

  it('handles ## Summary on single line with inline code and parentheses', () => {
    const rawResponse = `## Summary The Proxmox VE API/GUI proxy (\`pveproxy\`) is experiencing lock contention.

## Root Cause
High concurrency on /api2/json.`;

    const summary = extractCleanSummary(rawResponse);
    expect(summary).toBe('The Proxmox VE API/GUI proxy (pveproxy) is experiencing lock contention.');
    expect(summary).not.toContain('##');
  });

  it('handles markdown lists inside summary by collapsing into readable sentence', () => {
    const rawResponse = `## Summary:
Outage detected across multiple subsystems:
- Primary DB connection failed
- Secondary replica timed out

## Root Cause
Network split.`;

    const summary = extractCleanSummary(rawResponse);
    expect(summary).toBe('Outage detected across multiple subsystems: Primary DB connection failed Secondary replica timed out');
    expect(summary).not.toContain('-');
  });

  it('falls back gracefully on plain unstructured responses', () => {
    const rawResponse = 'Simple one-paragraph plain-text diagnosis of an outage without headers.';
    const summary = extractCleanSummary(rawResponse);
    expect(summary).toBe('Simple one-paragraph plain-text diagnosis of an outage without headers.');
  });

  it('returns empty string on empty input', () => {
    expect(extractCleanSummary('')).toBe('');
  });
});
