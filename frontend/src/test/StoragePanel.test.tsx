import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { StoragePanel } from '../components/storage/StoragePanel.tsx';
import * as settingsApi from '../api/settings.ts';
import * as systemApi from '../api/system.ts';
import * as aiApi from '../api/ai.ts';

describe('StoragePanel Component', () => {
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
      db_size_bytes: 18247000000,
      disk_free_bytes: 450000000000,
      disk_total_bytes: 1000000000000,
      history: [],
    });
    vi.spyOn(aiApi, 'fetchAiAudit').mockResolvedValue({
      items: mockAuditLogs,
      total: 1,
    });
  });

  it('renders storage metrics, retention slider, and storage trend chart', async () => {
    render(<StoragePanel />);

    await waitFor(() => {
      expect(screen.getByText('Storage, Retention & Audit History')).toBeInTheDocument();
      expect(screen.getByText('Storage & Retention')).toBeInTheDocument();
    });

    expect(screen.getByTestId('db-size-display')).toHaveTextContent('16.99 GB');
    expect(screen.getByTestId('disk-free-display')).toHaveTextContent('419.1 GB');
    expect(screen.getByText(/Log Retention Policy/i)).toBeInTheDocument();
  });

  it('renders a clean summary without ## Summary or markdown backticks in the audit table', async () => {
    render(<StoragePanel />);

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
    render(<StoragePanel />);

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

  it('toggles between Analysis Prompt and Full LLM Prompt in historical audit modal', async () => {
    render(<StoragePanel />);

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

  it('displays inline error banner when audit item deletion fails', async () => {
    vi.spyOn(aiApi, 'deleteAiAuditItem').mockRejectedValue(new Error('Network connection timeout'));

    render(<StoragePanel />);

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

    render(<StoragePanel />);

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

  it('updates retention policy through RetentionSlider', async () => {
    const updateSpy = vi.spyOn(settingsApi, 'updateSettings').mockResolvedValue({ status: 'ok' });

    render(<StoragePanel />);

    await waitFor(() => {
      expect(screen.getByText('Storage, Retention & Audit History')).toBeInTheDocument();
    });

    const slider = screen.getByRole('slider');
    fireEvent.change(slider, { target: { value: '21' } });

    const saveBtn = screen.getByRole('button', { name: /Save Retention/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith({ retention_days: 21 });
    });
  });

  it('renders mobile touch cards for AI audit log when viewport is under 768px', async () => {
    vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
      matches: query.includes('max-width: 767px'),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));

    render(<StoragePanel />);

    await waitFor(() => {
      expect(screen.getByText('AI Root-Cause Audit Log (1)')).toBeInTheDocument();
    });

    // In mobile view, desktop 6-column header "TIMESTAMP" is omitted
    expect(screen.queryByText('TIMESTAMP')).not.toBeInTheDocument();

    // Mobile card shows clean summary and model
    expect(screen.getByText('The technitium-sync service completed its sync successfully.')).toBeInTheDocument();
    expect(screen.getByText('gemini-3.7-flash')).toBeInTheDocument();
    expect(screen.getByText(/985 tok/i)).toBeInTheDocument();

    // View action button is present and opens modal
    const viewButton = screen.getByTitle('View analysis details');
    fireEvent.click(viewButton);

    await waitFor(() => {
      expect(screen.getByText('Historical AI Root-Cause Analysis')).toBeInTheDocument();
    });
  });
});
