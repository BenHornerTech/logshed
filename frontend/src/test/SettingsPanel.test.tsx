import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { SettingsPanel } from '../components/settings/SettingsPanel.tsx';
import * as settingsApi from '../api/settings.ts';
import * as systemApi from '../api/system.ts';
import * as aiApi from '../api/ai.ts';

describe('SettingsPanel AI Audit Log & Disclaimer', () => {
  const mockAuditLogs = [
    {
      id: 1,
      timestamp: '2026-09-04T08:20:45Z',
      source_alias: 'docker',
      app_name: 'technitium-sync',
      log_count: 5,
      user_context: null,
      model: 'gemini-3.7-flash',
      prompt_sent: 'Prompt text',
      response_text:
        '## Summary\nThe `technitium-sync` service completed its sync successfully.\n\n## Root Cause\nNo failure occurred.\n\n## Actionable Remediation\nContinue monitoring.',
      tokens_in: 800,
      tokens_out: 185,
      tokens_thoughts: 0,
      tokens_used: 985,
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({
      ai_provider: 'gemini',
      ai_model: 'gemini-3.7-flash',
      ai_api_key: '********',
      ai_base_url: null,
      retention_days: 14,
      max_retention_days: 30,
      has_ai_api_key: true,
    });

    vi.spyOn(systemApi, 'fetchStorageMetrics').mockResolvedValue({
      db_size_bytes: 1000000,
      disk_free_bytes: 500000000,
      disk_total_bytes: 1000000000,
      history: [],
    });
    vi.spyOn(aiApi, 'fetchAiAudit').mockResolvedValue({
      items: mockAuditLogs,
      total: 1,
    });
  });

  it('renders a clean summary without ## Summary or markdown backticks in the audit table', async () => {
    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText('AI Root-Cause Audit Log (1)')).toBeInTheDocument();
    });

    // Clean summary should strip '## Summary' and backticks around 'technitium-sync'
    const summaryCell = screen.getByText(
      'The technitium-sync service completed its sync successfully.'
    );
    expect(summaryCell).toBeInTheDocument();
    expect(summaryCell.textContent).not.toContain('## Summary');
    expect(summaryCell.textContent).not.toContain('`');
  });

  it('displays the AI advisory disclaimer banner inside the historical analysis detail modal', async () => {
    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText('AI Root-Cause Audit Log (1)')).toBeInTheDocument();
    });

    // Click "View" to open historical analysis modal
    const viewButton = screen.getByTitle('View analysis details');
    fireEvent.click(viewButton);

    await waitFor(() => {
      expect(screen.getByText('Historical AI Root-Cause Analysis')).toBeInTheDocument();
    });

    const disclaimerText =
      'AI root-cause analyses and remediation commands are advisory only. Always verify proposed commands and configurations before executing on systems. API calls consume tokens billed to your provider.';

    expect(screen.getByText(disclaimerText)).toBeInTheDocument();
  });

  it('renders AI System Instructions card, allows editing and saving ai_system_prompt', async () => {
    const updateSpy = vi.spyOn(settingsApi, 'updateSettings').mockResolvedValue({ status: 'ok' });

    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText(/AI System Instructions/i)).toBeInTheDocument();
    });

    const sysTextarea = screen.getByPlaceholderText('Enter system instructions...');
    expect((sysTextarea as HTMLTextAreaElement).value).toContain('expert systems engineer');

    // Edit system prompt
    fireEvent.change(sysTextarea, {
      target: { value: 'Custom instructions for homelab root-cause diagnosis.' },
    });

    const saveBtn = screen.getByRole('button', { name: /Save Application Settings/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          ai_system_prompt: 'Custom instructions for homelab root-cause diagnosis.',
        })
      );
    });
  });

  it('resets AI System Instructions to default when Reset to Default is clicked', async () => {
    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText(/AI System Instructions/i)).toBeInTheDocument();
    });

    const sysTextarea = screen.getByPlaceholderText('Enter system instructions...');
    fireEvent.change(sysTextarea, {
      target: { value: 'Temporary altered instructions' },
    });
    expect((sysTextarea as HTMLTextAreaElement).value).toBe('Temporary altered instructions');

    const resetBtn = screen.getByRole('button', { name: /Reset to Default/i });
    fireEvent.click(resetBtn);

    expect((sysTextarea as HTMLTextAreaElement).value).toContain('expert systems engineer');
  });

  it('toggles between Analysis Prompt and Full LLM Prompt in historical audit modal', async () => {
    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText('AI Root-Cause Audit Log (1)')).toBeInTheDocument();
    });

    // Click "View" to open historical analysis modal
    const viewButton = screen.getByTitle('View analysis details');
    fireEvent.click(viewButton);

    await waitFor(() => {
      expect(screen.getByText('Historical AI Root-Cause Analysis')).toBeInTheDocument();
    });

    // Expand prompt
    const togglePromptBtn = screen.getByText(/View Submitted Logs & Prompt/i);
    fireEvent.click(togglePromptBtn);

    expect(screen.getByRole('button', { name: 'Analysis Prompt' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Full LLM Prompt' })).toBeInTheDocument();

    // Default view shows analysis prompt
    expect(screen.getByText('Prompt text')).toBeInTheDocument();
    expect(screen.queryByText(/=== SYSTEM INSTRUCTIONS ===/)).not.toBeInTheDocument();

    // Toggle to Full LLM Prompt
    fireEvent.click(screen.getByRole('button', { name: 'Full LLM Prompt' }));
    expect(screen.getByText(/=== SYSTEM INSTRUCTIONS ===/)).toBeInTheDocument();
    expect(screen.getByText(/=== USER ANALYSIS PROMPT ===/)).toBeInTheDocument();
  });

  it('keeps Save button disabled/dimmed on initial load with masked API key, activates on edit, and shows inline feedback on save', async () => {
    const updateSpy = vi.spyOn(settingsApi, 'updateSettings').mockResolvedValue({ status: 'ok' });
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({
      ai_provider: 'gemini',
      ai_model: 'gemini-3.7-flash',
      ai_api_key: '********',
      ai_base_url: null,
      retention_days: 14,
      max_retention_days: 30,
      has_ai_api_key: true,
    });


    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Save Application Settings/i })).toBeInTheDocument();
    });

    const saveBtn = screen.getByRole('button', { name: /Save Application Settings/i });
    // Initial state: not dirty, button is disabled and dimmed
    expect(saveBtn).toBeDisabled();
    expect(saveBtn.className).toContain('opacity-40');
    expect(saveBtn.className).toContain('cursor-not-allowed');

    // Edit Default Model Name
    const modelInput = screen.getByPlaceholderText('gemini-2.5-flash');
    fireEvent.change(modelInput, { target: { value: 'gemini-2.5-pro' } });

    // Active state: dirty, button is highlighted and enabled
    expect(saveBtn).not.toBeDisabled();
    expect(saveBtn.className).toContain('bg-accent-600');
    expect(saveBtn.className).toContain('cursor-pointer');

    // Click Save
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          ai_model: 'gemini-2.5-pro',
        })
      );
      // Real-time inline feedback displayed adjacent to button
      expect(screen.getByText('Settings saved successfully!')).toBeInTheDocument();
    });

    // Baseline reloaded -> button returns to disabled and dimmed state
    await waitFor(() => {
      expect(saveBtn).toBeDisabled();
      expect(saveBtn.className).toContain('opacity-40');
    });
  });

  it('renders "Keys encrypted at rest" badge, card title, and explanatory Fernet caption', async () => {
    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText('On-Demand AI Provider Configuration (Keys encrypted at rest)')).toBeInTheDocument();
    });

    // Badge
    expect(screen.getByText('Keys encrypted at rest')).toBeInTheDocument();

    // Explanatory caption
    expect(
      screen.getByText(
        'Fernet encryption secures API keys against exposure in database exports, disk clones, and backups.'
      )
    ).toBeInTheDocument();
  });

  it('renders internal log severity dropdown, marks form dirty on change, and saves internal_log_level', async () => {
    const updateSpy = vi.spyOn(settingsApi, 'updateSettings').mockResolvedValue({ status: 'ok' });

    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText('Internal Application Logging')).toBeInTheDocument();
    });

    const select = screen.getByLabelText('Internal Log Severity Threshold') as HTMLSelectElement;
    expect(select.value).toBe('WARNING');

    // Change to ERROR
    fireEvent.change(select, { target: { value: 'ERROR' } });
    expect(select.value).toBe('ERROR');

    const saveBtn = screen.getByRole('button', { name: /Save Application Settings/i });
    expect(saveBtn).not.toBeDisabled();

    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          internal_log_level: 'ERROR',
        })
      );
    });
  });
});

