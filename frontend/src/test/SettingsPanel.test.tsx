import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { SettingsPanel } from '../components/settings/SettingsPanel.tsx';
import * as settingsApi from '../api/settings.ts';
import * as systemApi from '../api/system.ts';
import * as aiApi from '../api/ai.ts';
import { DEFAULT_SYSTEM_PROMPT } from '../utils/aiPrompt.ts';

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
    vi.spyOn(aiApi, 'getAiModels').mockResolvedValue({
      provider: 'gemini',
      models: [
        { id: 'gemini-3.7-flash', name: 'Gemini 3.7 Flash', supports_thinking: true },
        { id: 'gemini-2.5-flash', name: 'Gemini 2.5 Flash', supports_thinking: true },
        { id: 'gemini-2.5-pro', name: 'Gemini 2.5 Pro', supports_thinking: true },
        { id: 'gemini-2.0-flash', name: 'Gemini 2.0 Flash', supports_thinking: false },
        { id: 'gemini-1.5-flash', name: 'Gemini 1.5 Flash', supports_thinking: false },
      ],
      has_api_key: true,
      cached_at: null,
      is_live: true,
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

  it('only displays Reset to Default button when AI System Instructions are modified from default', async () => {
    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText(/AI System Instructions/i)).toBeInTheDocument();
    });

    const sysTextarea = screen.getByPlaceholderText('Enter system instructions...');

    // On initial load with default prompt, Reset to Default button should NOT be visible
    expect(screen.queryByRole('button', { name: /Reset to Default/i })).toBeNull();

    // Alter system instructions
    fireEvent.change(sysTextarea, {
      target: { value: 'Temporary altered instructions' },
    });
    expect((sysTextarea as HTMLTextAreaElement).value).toBe('Temporary altered instructions');

    // Reset button should now appear
    const resetBtn = screen.getByRole('button', { name: /Reset to Default/i });
    expect(resetBtn).toBeInTheDocument();

    // Clicking Reset button resets instructions and hides the button
    fireEvent.click(resetBtn);
    expect((sysTextarea as HTMLTextAreaElement).value).toContain('expert systems engineer');
    expect(screen.queryByRole('button', { name: /Reset to Default/i })).toBeNull();

    // Modifying again shows button, then typing back default hides it without clicking
    fireEvent.change(sysTextarea, {
      target: { value: 'Another custom instruction' },
    });
    expect(screen.getByRole('button', { name: /Reset to Default/i })).toBeInTheDocument();

    fireEvent.change(sysTextarea, {
      target: { value: DEFAULT_SYSTEM_PROMPT },
    });
    expect(screen.queryByRole('button', { name: /Reset to Default/i })).toBeNull();
  });

  it('displays Reset to Default immediately on load when settings contain a custom system prompt', async () => {
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({
      ai_provider: 'gemini',
      ai_model: 'gemini-3.7-flash',
      ai_api_key: '********',
      ai_base_url: null,
      ai_system_prompt: 'Pre-existing custom system prompt from database',
      retention_days: 14,
      max_retention_days: 30,
      has_ai_api_key: true,
    });

    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText(/AI System Instructions/i)).toBeInTheDocument();
    });

    // Custom prompt loaded -> Reset to Default button should be visible immediately
    const resetBtn = screen.getByRole('button', { name: /Reset to Default/i });
    expect(resetBtn).toBeInTheDocument();

    // Clicking it resets to DEFAULT_SYSTEM_PROMPT and hides the button
    fireEvent.click(resetBtn);
    const sysTextarea = screen.getByPlaceholderText('Enter system instructions...');
    expect((sysTextarea as HTMLTextAreaElement).value).toBe(DEFAULT_SYSTEM_PROMPT);
    expect(screen.queryByRole('button', { name: /Reset to Default/i })).toBeNull();
  });

  it('does not display Reset to Default when text matches default with Windows CRLF line endings', async () => {
    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText(/AI System Instructions/i)).toBeInTheDocument();
    });

    const sysTextarea = screen.getByPlaceholderText('Enter system instructions...');
    const crlfPrompt = DEFAULT_SYSTEM_PROMPT.replace(/\n/g, '\r\n');

    fireEvent.change(sysTextarea, {
      target: { value: crlfPrompt },
    });

    // Despite CRLF differences, semantic instructions match default so Reset button must NOT appear
    expect(screen.queryByRole('button', { name: /Reset to Default/i })).toBeNull();
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

    // Edit Default Model Name via dropdown
    const modelSelect = screen.getByDisplayValue(/gemini-3.7-flash/i);
    fireEvent.change(modelSelect, { target: { value: 'gemini-2.5-flash' } });

    // Active state: dirty, button is highlighted and enabled
    expect(saveBtn).not.toBeDisabled();
    expect(saveBtn.className).toContain('bg-accent-600');
    expect(saveBtn.className).toContain('cursor-pointer');

    // Click Save
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          ai_model: 'gemini-2.5-flash',
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

  it('renders model discovery dropdown and allows selecting discovered models', async () => {
    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText(/On-Demand AI Provider Configuration/i)).toBeInTheDocument();
      expect(screen.getByDisplayValue(/gemini-3.7-flash/i)).toBeInTheDocument();
    });

    // Gemini provider default model is selected in dropdown
    const geminiSelect = screen.getByDisplayValue(/gemini-3.7-flash/i);
    expect(geminiSelect).toBeInTheDocument();

    // Discovered models include reasoning badges
    expect(screen.getAllByText(/gemini-3.7-flash \[Reasoning\]/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/gemini-2.5-flash \[Reasoning\]/i).length).toBeGreaterThan(0);

    // Switch to OpenAI
    const providerSelect = screen.getByDisplayValue('Google Gemini');
    fireEvent.change(providerSelect, { target: { value: 'openai' } });

    await waitFor(() => {
      expect(aiApi.getAiModels).toHaveBeenCalledWith('openai', false);
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

  it('displays inline error banner when audit item deletion fails', async () => {
    vi.spyOn(aiApi, 'deleteAiAuditItem').mockRejectedValue(new Error('Network connection timeout'));

    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText('AI Root-Cause Audit Log (1)')).toBeInTheDocument();
    });

    const deleteBtn = screen.getByTitle('Delete this analysis');
    fireEvent.click(deleteBtn);

    await waitFor(() => {
      expect(screen.getByText(/Failed to delete AI analysis: Network connection timeout/i)).toBeInTheDocument();
    });
  });

  it('displays inline error banner when clearing all audit items fails', async () => {
    vi.spyOn(aiApi, 'clearAiAuditLog').mockRejectedValue(new Error('Database write failure'));

    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText('AI Root-Cause Audit Log (1)')).toBeInTheDocument();
    });

    const clearAllBtn = screen.getByRole('button', { name: /Clear All/i });
    fireEvent.click(clearAllBtn);

    await waitFor(() => {
      expect(screen.getByText('Permanently delete all historical AI analyses?')).toBeInTheDocument();
    });

    const confirmDeleteBtn = screen.getByRole('button', { name: /Delete All/i });
    fireEvent.click(confirmDeleteBtn);

    await waitFor(() => {
      expect(screen.getAllByText(/Failed to clear AI audit log: Database write failure/i).length).toBeGreaterThan(0);
    });
  });

  it('renders and updates fallback models setting', async () => {
    const updateSpy = vi.spyOn(settingsApi, 'updateSettings').mockResolvedValue({ status: 'ok' });

    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText('Fallback Models (Sequential Order)')).toBeInTheDocument();
    });

    // Select a fallback model from the add dropdown
    const fallbackSelect = screen.getByDisplayValue('-- Add Fallback Model --');
    fireEvent.change(fallbackSelect, { target: { value: 'gemini-2.5-flash' } });

    const addBtn = screen.getByRole('button', { name: /Add/i });
    fireEvent.click(addBtn);

    await waitFor(() => {
      expect(screen.getByText('1st Fallback')).toBeInTheDocument();
      expect(screen.getByText('gemini-2.5-flash')).toBeInTheDocument();
    });

    const saveButton = screen.getByRole('button', { name: /Save Application Settings/i });
    expect(saveButton).not.toBeDisabled();
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          ai_fallback_models: 'gemini-2.5-flash',
        })
      );
    });
  });

  it('renders configured fallback models with proper ordinal badges (1st, 2nd, 3rd)', async () => {
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({
      ai_provider: 'gemini',
      ai_model: 'gemini-3.7-flash',
      ai_fallback_models: 'gemini-2.5-flash, gemini-2.5-pro, gemini-1.5-flash',
      ai_api_key: '********',
      ai_base_url: null,
      retention_days: 14,
      max_retention_days: 30,
      has_ai_api_key: true,
    });

    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText('1st Fallback')).toBeInTheDocument();
      expect(screen.getByText('2nd Fallback')).toBeInTheDocument();
      expect(screen.getByText('3rd Fallback')).toBeInTheDocument();
    });
  });

  it('filters out fallback models from primary model dropdown and auto-prunes fallback if selected as primary', async () => {
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({
      ai_provider: 'gemini',
      ai_model: 'gemini-3.7-flash',
      ai_fallback_models: 'gemini-2.5-flash',
      ai_api_key: '********',
      ai_base_url: null,
      retention_days: 14,
      max_retention_days: 30,
      has_ai_api_key: true,
    });

    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText('1st Fallback')).toBeInTheDocument();
      expect(screen.getByText('gemini-2.5-flash')).toBeInTheDocument();
    });

    // In Primary Model dropdown, gemini-2.5-flash should NOT be available as an option because it is already a fallback
    const primarySelect = screen.getByDisplayValue(/gemini-3.7-flash/);
    const options = Array.from(primarySelect.querySelectorAll('option')).map((o) => o.value);
    expect(options).toContain('gemini-3.7-flash');
    expect(options).not.toContain('gemini-2.5-flash');

    // If user switches to custom mode and enters gemini-2.5-flash as primary
    const useDropdownBtn = screen.queryByRole('button', { name: /Use dropdown instead/i });
    expect(useDropdownBtn).toBeNull();

    // Select custom model option from primary dropdown
    fireEvent.change(primarySelect, { target: { value: '__custom__' } });
    const customInput = screen.getByPlaceholderText(/DEFAULT_AI_MODEL|gemini-3.7-flash/i);
    fireEvent.change(customInput, { target: { value: 'gemini-2.5-flash' } });

    // gemini-2.5-flash should now be pruned from fallback models list
    await waitFor(() => {
      expect(screen.queryByText('1st Fallback')).toBeNull();
      expect(screen.getByText(/No fallback models configured/i)).toBeInTheDocument();
    });
  });
});


