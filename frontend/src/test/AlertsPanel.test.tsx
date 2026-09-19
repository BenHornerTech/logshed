import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AlertsPanel } from '../components/alerts/AlertsPanel.tsx';
import * as alertsApi from '../api/alerts.ts';
import * as notificationsApi from '../api/notifications.ts';

const mockRules = [
  {
    id: 1,
    name: 'SSH Brute-Force Detection',
    rule_type: 'threshold',
    channel_id: 1,
    filter_app: 'sshd',
    filter_severity: null,
    match_pattern: 'Failed password',
    threshold_count: 5,
    window_seconds: 60,
    cooldown_seconds: 300,
    ai_enrichment: true,
    is_enabled: true,
    trigger_count: 2,
    last_triggered_at: '2026-09-18T10:00:00Z',
    suppress_until: null,
    created_at: '2026-09-18T09:00:00Z',
  },
];

const mockPresets = [
  {
    id: 'ssh_bruteforce',
    name: 'SSH Brute-Force Detection',
    description: 'Detects repeated failed SSH authentication attempts from offending IP addresses.',
    rule_type: 'threshold',
    filter_app: 'sshd',
    filter_severity: null,
    match_pattern: 'Failed password',
    threshold_count: 5,
    window_seconds: 60,
    cooldown_seconds: 300,
    ai_enrichment: true,
  },
  {
    id: 'oom_killer',
    name: 'Kernel Out-Of-Memory (OOM) Kill',
    description: 'Detects Linux kernel out-of-memory killer invocations.',
    rule_type: 'pattern',
    filter_app: null,
    filter_severity: null,
    match_pattern: 'Out of memory: Kill process',
    threshold_count: 1,
    window_seconds: 60,
    cooldown_seconds: 300,
    ai_enrichment: true,
  },
];

const mockHistory = {
  items: [
    {
      id: 10,
      rule_id: 1,
      rule_name: 'SSH Brute-Force Detection',
      channel_id: 1,
      trigger_count: 5,
      sample_log: 'Failed password for root from 192.168.1.100 port 22',
      incident_summary: 'Brute-force attack from host 192.168.1.100 against root.',
      ai_enrichment: true,
      triggered_at: '2026-09-18T10:00:00Z',
    },
  ],
  total: 1,
  limit: 50,
  offset: 0,
};

const mockChannels = [
  {
    id: 1,
    name: 'Discord Ops',
    url: 'discord://webhook/********',
    is_enabled: true,
    created_at: '2026-09-18T08:00:00Z',
    updated_at: '2026-09-18T08:00:00Z',
  },
];

describe('AlertsPanel Component', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(alertsApi, 'fetchAlertRules').mockResolvedValue(mockRules);
    vi.spyOn(alertsApi, 'fetchSecurityPresets').mockResolvedValue(mockPresets);
    vi.spyOn(alertsApi, 'fetchAlertHistory').mockResolvedValue(mockHistory);
    vi.spyOn(notificationsApi, 'fetchNotificationChannels').mockResolvedValue(mockChannels);
  });

  it('renders alert rules and switches between tabs', async () => {
    render(<AlertsPanel />);

    await waitFor(() => {
      expect(screen.getByText('SSH Brute-Force Detection')).toBeInTheDocument();
      expect(screen.getByText('AI Enriched')).toBeInTheDocument();
      expect(screen.getByText('Fired 2x')).toBeInTheDocument();
    });

    // Switch to Quick Rules tab
    const presetsTab = screen.getByRole('button', { name: /Quick Rules/i });
    fireEvent.click(presetsTab);

    await waitFor(() => {
      expect(screen.getByText('1-Click Quick Rule Presets')).toBeInTheDocument();
      expect(screen.getByText('Kernel Out-Of-Memory (OOM) Kill')).toBeInTheDocument();
    });

    // Switch to Incident History tab
    const historyTab = screen.getByRole('button', { name: /Incident History/i });
    fireEvent.click(historyTab);

    await waitFor(() => {
      expect(screen.getByText('Alert Firing Log')).toBeInTheDocument();
      expect(screen.getByText('Complete')).toBeInTheDocument();
    });

    // Expand row to view diagnosis summary
    const row = screen.getByText('SSH Brute-Force Detection');
    fireEvent.click(row);

    await waitFor(() => {
      expect(screen.getByText('Brute-force attack from host 192.168.1.100 against root.')).toBeInTheDocument();
    });
  });

  it('allows toggling an alert rule status', async () => {
    const updateSpy = vi.spyOn(alertsApi, 'updateAlertRule').mockResolvedValue({
      ...mockRules[0],
      is_enabled: false,
    });

    render(<AlertsPanel />);

    await waitFor(() => {
      expect(screen.getByText('SSH Brute-Force Detection')).toBeInTheDocument();
    });

    const toggleBtn = screen.getByTitle('Click to disable');
    fireEvent.click(toggleBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith(1, { is_enabled: false });
    });
  });

  it('opens create modal and submits a new alert rule', async () => {
    const createSpy = vi.spyOn(alertsApi, 'createAlertRule').mockResolvedValue({
      id: 2,
      name: 'Nginx 502 Bad Gateway',
      rule_type: 'threshold',
      channel_id: 1,
      filter_app: 'nginx',
      filter_severity: 3,
      match_pattern: '502 Bad Gateway',
      threshold_count: 10,
      window_seconds: 60,
      cooldown_seconds: 300,
      ai_enrichment: true,
      is_enabled: true,
      trigger_count: 0,
      created_at: '2026-09-18T11:00:00Z',
    });

    render(<AlertsPanel />);

    await waitFor(() => {
      expect(screen.getByText('SSH Brute-Force Detection')).toBeInTheDocument();
    });

    const newBtn = screen.getByRole('button', { name: /New Alert Rule/i });
    fireEvent.click(newBtn);

    expect(screen.getByRole('heading', { name: 'Create New Alert Rule' })).toBeInTheDocument();

    const nameInput = screen.getByPlaceholderText('e.g. Critical Auth Failure Spike');
    fireEvent.change(nameInput, { target: { value: 'Nginx 502 Bad Gateway' } });

    const saveBtn = screen.getByRole('button', { name: 'Create Rule' });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(createSpy).toHaveBeenCalled();
      expect(screen.getByText('Nginx 502 Bad Gateway')).toBeInTheDocument();
    });
  });

  it('installs a 1-click quick rule preset', async () => {
    const installSpy = vi.spyOn(alertsApi, 'installSecurityPreset').mockResolvedValue({
      id: 3,
      name: 'Kernel Out-Of-Memory (OOM) Kill',
      rule_type: 'pattern',
      channel_id: null,
      filter_app: null,
      filter_severity: null,
      match_pattern: 'Out of memory: Kill process',
      threshold_count: 1,
      window_seconds: 60,
      cooldown_seconds: 300,
      ai_enrichment: true,
      is_enabled: true,
      trigger_count: 0,
      created_at: '2026-09-18T12:00:00Z',
    });

    render(<AlertsPanel />);

    await waitFor(() => {
      expect(screen.getByText('SSH Brute-Force Detection')).toBeInTheDocument();
    });

    // Go to presets tab
    const presetsTab = screen.getByRole('button', { name: /Quick Rules/i });
    fireEvent.click(presetsTab);

    await waitFor(() => {
      expect(screen.getByText('Kernel Out-Of-Memory (OOM) Kill')).toBeInTheDocument();
    });

    const installBtns = screen.getAllByRole('button', { name: /Install Rule/i });
    fireEvent.click(installBtns[0]);

    await waitFor(() => {
      expect(installSpy).toHaveBeenCalled();
    });
  });

  it('renders failed status badge for failed AI analysis and shows Test Rule button', async () => {
    vi.spyOn(alertsApi, 'fetchAlertHistory').mockResolvedValue({
      items: [
        {
          id: 11,
          rule_id: 1,
          rule_name: 'SSH Brute-Force Detection',
          channel_id: 1,
          trigger_count: 5,
          sample_log: 'Failed password for root',
          incident_summary: 'AI analysis failed: Gemini API error 504 DEADLINE_EXCEEDED',
          ai_enrichment: true,
          triggered_at: '2026-09-18T10:05:00Z',
        },
      ],
      total: 1,
      limit: 50,
      offset: 0,
    });

    render(<AlertsPanel />);

    await waitFor(() => {
      expect(screen.getByText('SSH Brute-Force Detection')).toBeInTheDocument();
    });

    // Verify Test Rule button
    const testBtn = screen.getByTitle('Test Rule');
    expect(testBtn).toBeInTheDocument();

    // Switch to Incident History tab
    const historyTab = screen.getByRole('button', { name: /Incident History/i });
    fireEvent.click(historyTab);

    await waitFor(() => {
      expect(screen.getByText('Failed')).toBeInTheDocument();
    });
  });

  it('renders Complete badge when AI diagnosis describes a system failure without AI engine error', async () => {
    vi.spyOn(alertsApi, 'fetchAlertHistory').mockResolvedValue({
      items: [
        {
          id: 12,
          rule_id: 1,
          rule_name: 'Kernel Hardware Watchdog',
          channel_id: 1,
          trigger_count: 1,
          sample_log: 'Critical hardware watchdog fired',
          incident_summary: 'The system experienced a critical emergency failure triggered by a hardware watchdog timer expiration. Root Cause: Kernel deadlock. Remediation: Check hardware health.',
          ai_enrichment: true,
          ai_model: 'gemini-2.5-flash',
          triggered_at: '2026-09-18T10:10:00Z',
        },
      ],
      total: 1,
      limit: 50,
      offset: 0,
    });

    render(<AlertsPanel />);

    const historyTab = screen.getByRole('button', { name: /Incident History/i });
    fireEvent.click(historyTab);

    await waitFor(() => {
      expect(screen.getByText('Kernel Hardware Watchdog')).toBeInTheDocument();
      expect(screen.getByText('Complete')).toBeInTheDocument();
      expect(screen.queryByText('Failed')).not.toBeInTheDocument();
    });

    // Click View button to expand details
    const viewBtn = screen.getByRole('button', { name: 'View' });
    fireEvent.click(viewBtn);

    await waitFor(() => {
      expect(screen.getByText('Model:')).toBeInTheDocument();
      expect(screen.getByText('gemini-2.5-flash')).toBeInTheDocument();
      expect(screen.getByText('AI Incident Diagnosis & Remediation')).toBeInTheDocument();
      expect(screen.getByText('Triggering Log Snippet')).toBeInTheDocument();
      expect(screen.getByTitle('Copy triggering log')).toBeInTheDocument();
    });
  });

  it('prompts confirmation modal when deleting a single incident history item', async () => {
    const deleteSpy = vi.spyOn(alertsApi, 'deleteAlertHistoryItem').mockResolvedValue({ success: true } as any);

    render(<AlertsPanel />);

    // Switch to Incident History tab
    const historyTab = screen.getByRole('button', { name: /Incident History/i });
    fireEvent.click(historyTab);

    await waitFor(() => {
      expect(screen.getByText('SSH Brute-Force Detection')).toBeInTheDocument();
    });

    const deleteBtn = screen.getByTitle('Delete incident record');
    fireEvent.click(deleteBtn);

    // Confirmation modal should appear
    expect(screen.getByRole('heading', { name: 'Delete Incident Record' })).toBeInTheDocument();
    expect(screen.getByText(/Are you sure you want to delete this incident record/i)).toBeInTheDocument();

    // Confirm deletion
    const confirmBtn = screen.getByRole('button', { name: 'Delete Record' });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(deleteSpy).toHaveBeenCalledWith(10);
      expect(screen.queryByRole('heading', { name: 'Delete Incident Record' })).toBeNull();
    });
  });

  it('opens test modal with FlaskConical icon and allows running test', async () => {
    const testSpy = vi.spyOn(alertsApi, 'testAlertRule').mockResolvedValue({
      matched: true,
      extracted_ip: '192.168.1.100',
    });

    render(<AlertsPanel />);

    await waitFor(() => {
      expect(screen.getByText('SSH Brute-Force Detection')).toBeInTheDocument();
    });

    const testBtn = screen.getByTitle('Test Rule');
    fireEvent.click(testBtn);

    expect(screen.getByRole('heading', { name: /Dry-Run Test:/i })).toBeInTheDocument();

    const runBtn = screen.getByRole('button', { name: 'Run Test' });
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(testSpy).toHaveBeenCalled();
      expect(screen.getByText('Pattern Matched Successfully')).toBeInTheDocument();
      expect(screen.getByText('192.168.1.100')).toBeInTheDocument();
    });
  });
});
