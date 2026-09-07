import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AiAnalysisModal } from '../components/ai/AiAnalysisModal.tsx';
import { LogEntry } from '../types.ts';
import * as aiApi from '../api/ai.ts';
import * as clipboardUtil from '../utils/clipboard.ts';

describe('AiAnalysisModal Component (Items #10, #23, #26, #27, #28)', () => {
  const sampleLogs: LogEntry[] = [
    {
      id: 1,
      timestamp: '2026-09-04T08:00:00.000Z',
      received_at: '2026-09-04T08:00:00.100Z',
      source_ip: '192.168.1.1',
      source_alias: 'router',
      app_name: 'dnsmasq',
      facility: 1,
      severity: 3,
      message: 'query failed: upstream DNS server refused connection',
      raw: 'raw dnsmasq log',
    },
    {
      id: 2,
      timestamp: '2026-09-04T08:00:01.000Z',
      received_at: '2026-09-04T08:00:01.100Z',
      source_ip: '192.168.1.1',
      source_alias: 'router',
      app_name: 'dnsmasq',
      facility: 1,
      severity: 4,
      message: 'retrying upstream DNS server at 1.1.1.1:53',
      raw: 'raw dnsmasq retry',
    },
  ];

  const samplePreview = {
    redacted_prompt:
      "### System Metadata\n- Host / Source: router\n- Container / Service: dnsmasq\n- Total Selected Logs: 2\n\n### Redacted Log Stream (Chronological)\n```\n[2026-09-04T08:00:00.000Z] [dnsmasq] query failed: upstream DNS server refused connection\n[2026-09-04T08:00:01.000Z] [dnsmasq] retrying upstream DNS server at 1.1.1.1:53\n```\n\nPlease review these logs and provide Summary, Root Cause, and Actionable Remediation.",
    estimated_tokens: 280,
    provider: 'gemini',
    model: 'gemini-3.7-flash',
    log_count: 2,
    source_alias: 'router',
    app_name: 'dnsmasq',
    system_prompt:
      'You are an expert systems engineer, site reliability engineer (SRE), and Linux/Docker administrator.\nReview the following redacted server/container logs and provide a structured diagnosis in Markdown format.\n\nYour response MUST include the following three sections with exact headers:\n## Summary\nA concise 1-2 sentence overview of the issue.\n\n## Root Cause\nA detailed explanation of why the event or failure occurred based on the log evidence.\n\n## Actionable Remediation\nStep-by-step commands, configuration fixes, or debugging steps to resolve the issue.',
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(aiApi, 'previewAiPrompt').mockResolvedValue(samplePreview);
    vi.spyOn(aiApi, 'diagnoseLogs').mockResolvedValue({
      summary: 'DNS server connection refused.',
      root_cause: 'Upstream 1.1.1.1 DNS is unreachable.',
      remediation: 'Check firewall routing and DNS configuration.',
      model_used: 'gemini-3.7-flash',
      tokens_in: 285,
      tokens_out: 45,
      tokens_thoughts: 0,
      tokens_used: 330,
      audit_id: 12,
    });
  });

  it('renders an editable textarea instead of read-only div with full prompt visibility', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    await waitFor(() => {
      expect(aiApi.previewAiPrompt).toHaveBeenCalledWith({ log_ids: [1, 2] });
    });

    // The prompt is rendered inside an editable textarea
    const promptTextarea = await screen.findByPlaceholderText('Redacted prompt...');
    expect(promptTextarea.tagName.toLowerCase()).toBe('textarea');
    expect((promptTextarea as HTMLTextAreaElement).value).toContain('### System Metadata');
    expect((promptTextarea as HTMLTextAreaElement).value).toContain('query failed: upstream DNS server refused connection');

    // Does NOT have max-h-48 CSS height clipping
    expect(promptTextarea.className).not.toContain('max-h-48');
  });

  it('allows inline prompt editing and displays (modified) tag and Reset Prompt button', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    const promptTextarea = await screen.findByPlaceholderText('Redacted prompt...');
    expect(screen.queryByText('Reset Prompt')).not.toBeInTheDocument();
    expect(screen.queryByText('(modified)')).not.toBeInTheDocument();

    // Directly edit prompt in textarea
    fireEvent.change(promptTextarea, {
      target: { value: 'Custom operator edited prompt with pruned logs' },
    });

    expect((promptTextarea as HTMLTextAreaElement).value).toBe('Custom operator edited prompt with pruned logs');
    expect(screen.getByText('(modified)')).toBeInTheDocument();
    expect(screen.getByText('Reset Prompt')).toBeInTheDocument();

    // Clicking Reset Prompt restores the original generated prompt
    fireEvent.click(screen.getByText('Reset Prompt'));
    expect((promptTextarea as HTMLTextAreaElement).value).toContain('### System Metadata');
    expect(screen.queryByText('(modified)')).not.toBeInTheDocument();
    expect(screen.queryByText('Reset Prompt')).not.toBeInTheDocument();
  });

  it('displays accurate token count and informational tooltip', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    await screen.findByPlaceholderText('Redacted prompt...');

    const tooltipText =
      'Estimated prompt/input tokens only (includes system instructions and metadata). Does not include model thinking or response output tokens.';

    const tokenBadges = screen.getAllByTitle(tooltipText);
    expect(tokenBadges.length).toBeGreaterThan(0);
    expect(tokenBadges[0].textContent).toMatch(/~\d+ tokens/);
  });

  it('renders the AI advisory disclaimer banner above the action button with exact text', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    await screen.findByPlaceholderText('Redacted prompt...');

    const disclaimerText =
      'AI root-cause analyses and remediation commands are advisory only. Always verify proposed commands and configurations before executing on systems. API calls consume tokens billed to your provider.';

    expect(screen.getByText(disclaimerText)).toBeInTheDocument();
  });

  it('renders the log redaction notice reminding the user of their responsibility', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    await screen.findByPlaceholderText('Redacted prompt...');

    expect(screen.getByText('Redaction Notice:')).toBeInTheDocument();
    expect(
      screen.getByText(/Automated credential scrubbing operates on a best-effort basis/i)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Please review the prompt above before sending/i)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/you are responsible for the contents and sensitive data you transmit to external AI providers/i)
    ).toBeInTheDocument();
  });

  it('dispatches prompt_override to diagnoseLogs when prompt was edited by operator', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    const promptTextarea = await screen.findByPlaceholderText('Redacted prompt...');

    fireEvent.change(promptTextarea, {
      target: { value: 'Pruned prompt by operator for faster diagnosis.' },
    });

    const runBtn = screen.getByRole('button', { name: /Run AI Analysis/i });
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(aiApi.diagnoseLogs).toHaveBeenCalledWith(
        expect.objectContaining({
          log_ids: [1, 2],
          prompt_override: 'Pruned prompt by operator for faster diagnosis.',
        })
      );
    });
  });

  it('copies the edited prompt to clipboard when copy button is clicked', async () => {
    const copySpy = vi.spyOn(clipboardUtil, 'copyToClipboard').mockResolvedValue(true);

    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    const promptTextarea = await screen.findByPlaceholderText('Redacted prompt...');

    fireEvent.change(promptTextarea, {
      target: { value: 'My custom prompt to copy' },
    });

    const copyBtn = screen.getByRole('button', { name: /Copy Prompt/i });
    fireEvent.click(copyBtn);

    await waitFor(() => {
      expect(copySpy).toHaveBeenCalledWith('My custom prompt to copy');
    });
  });

  it('toggles between Analysis Prompt and Full LLM Prompt views and copies the active view', async () => {
    const copySpy = vi.spyOn(clipboardUtil, 'copyToClipboard').mockResolvedValue(true);

    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    const promptTextarea = await screen.findByPlaceholderText('Redacted prompt...');
    expect((promptTextarea as HTMLTextAreaElement).value).not.toContain('=== SYSTEM INSTRUCTIONS ===');

    // Click "Full LLM Prompt" toggle button
    const fullPromptBtn = screen.getByRole('button', { name: 'Full LLM Prompt' });
    fireEvent.click(fullPromptBtn);

    // Textarea now shows full envelope
    expect((promptTextarea as HTMLTextAreaElement).value).toContain('=== SYSTEM INSTRUCTIONS ===');
    expect((promptTextarea as HTMLTextAreaElement).value).toContain('=== USER ANALYSIS PROMPT ===');

    // Copying now copies the full envelope
    const copyBtn = screen.getByRole('button', { name: /Copy Prompt/i });
    fireEvent.click(copyBtn);

    await waitFor(() => {
      expect(copySpy).toHaveBeenCalledWith(
        expect.stringContaining('=== SYSTEM INSTRUCTIONS ===')
      );
    });

    // Switch back to "Analysis Prompt"
    const analysisPromptBtn = screen.getByRole('button', { name: 'Analysis Prompt' });
    fireEvent.click(analysisPromptBtn);
    expect((promptTextarea as HTMLTextAreaElement).value).not.toContain('=== SYSTEM INSTRUCTIONS ===');
  });

  it('allows editing system prompt in Full LLM Prompt view and passes system_prompt_override to diagnoseLogs', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    await screen.findByPlaceholderText('Redacted prompt...');

    // Switch to Full LLM Prompt
    fireEvent.click(screen.getByRole('button', { name: 'Full LLM Prompt' }));

    const fullPromptTextarea = screen.getByPlaceholderText('Full LLM prompt envelope...');
    fireEvent.change(fullPromptTextarea, {
      target: {
        value:
          '=== SYSTEM INSTRUCTIONS ===\nCustom operator persona for network triage.\n\n=== USER ANALYSIS PROMPT ===\nCustom user logs payload.',
      },
    });

    expect(screen.getByText('(modified)')).toBeInTheDocument();

    const runBtn = screen.getByRole('button', { name: /Run AI Analysis/i });
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(aiApi.diagnoseLogs).toHaveBeenCalledWith(
        expect.objectContaining({
          log_ids: [1, 2],
          prompt_override: 'Custom user logs payload.',
          system_prompt_override: 'Custom operator persona for network triage.',
        })
      );
    });
  });

  it('displays a clear guardrail error when more than 200 logs are selected without calling preview API', async () => {
    const manyLogs: LogEntry[] = Array.from({ length: 250 }, (_, i) => ({
      id: i + 1,
      timestamp: '2026-09-04T08:00:00.000Z',
      received_at: '2026-09-04T08:00:00.100Z',
      source_ip: '192.168.1.1',
      source_alias: 'router',
      app_name: 'dnsmasq',
      facility: 1,
      severity: 3,
      message: `log line ${i}`,
      raw: `raw log ${i}`,
    }));

    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={manyLogs}
      />
    );

    expect(await screen.findByText(/Cannot analyze more than 200 logs at once/i)).toBeInTheDocument();
    expect(screen.getAllByText(/250 logs selected/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/\(Max 200 logs allowed\)/i)).toBeInTheDocument();
    expect(aiApi.previewAiPrompt).not.toHaveBeenCalled();
  });

  it('hides Reset Prompt button when user manually reverts changes back to default', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    const promptTextarea = await screen.findByPlaceholderText('Redacted prompt...');
    const originalPrompt = (promptTextarea as HTMLTextAreaElement).value;

    expect(screen.queryByText('Reset Prompt')).not.toBeInTheDocument();

    // Modify prompt
    fireEvent.change(promptTextarea, {
      target: { value: 'Changed prompt text' },
    });
    expect(screen.getByText('Reset Prompt')).toBeInTheDocument();

    // Revert back to original prompt
    fireEvent.change(promptTextarea, {
      target: { value: originalPrompt },
    });
    expect(screen.queryByText('Reset Prompt')).not.toBeInTheDocument();
  });

  it('shows Reset Prompt button when system instructions are modified in full prompt view mode and resets on click', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    await screen.findByPlaceholderText('Redacted prompt...');

    // Switch to Full LLM Prompt mode
    fireEvent.click(screen.getByRole('button', { name: 'Full LLM Prompt' }));

    const fullPromptTextarea = screen.getByPlaceholderText('Full LLM prompt envelope...');
    expect(screen.queryByText('Reset Prompt')).not.toBeInTheDocument();

    // Modify only the system prompt portion in full view
    const modifiedFullEnvelope =
      '=== SYSTEM INSTRUCTIONS ===\nAltered system instructions.\n\n=== USER ANALYSIS PROMPT ===\n' +
      samplePreview.redacted_prompt;

    fireEvent.change(fullPromptTextarea, {
      target: { value: modifiedFullEnvelope },
    });

    expect(screen.getByText('Reset Prompt')).toBeInTheDocument();

    // Click Reset Prompt
    fireEvent.click(screen.getByText('Reset Prompt'));
    expect(screen.queryByText('Reset Prompt')).not.toBeInTheDocument();
    expect((fullPromptTextarea as HTMLTextAreaElement).value).toContain(samplePreview.system_prompt);
  });

  it('does not display Reset Prompt when prompt text matches default with CRLF line endings', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    const promptTextarea = await screen.findByPlaceholderText('Redacted prompt...');
    const crlfPrompt = samplePreview.redacted_prompt.replace(/\n/g, '\r\n');

    fireEvent.change(promptTextarea, {
      target: { value: crlfPrompt },
    });

    expect(screen.queryByText('Reset Prompt')).not.toBeInTheDocument();
  });

  it('keeps prompt in sync with situational context when unedited, and shows Reset Prompt only when edited', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    const promptTextarea = await screen.findByPlaceholderText('Redacted prompt...');
    const contextTextarea = screen.getByPlaceholderText(/Occurred immediately following network switch/i);

    // Initial load: Reset Prompt not shown
    expect(screen.queryByText('Reset Prompt')).not.toBeInTheDocument();

    // Type situational context
    fireEvent.change(contextTextarea, {
      target: { value: 'Container restarted 3 times' },
    });

    // Prompt updates automatically with context and Reset Prompt is NOT shown because it is not diverged
    expect((promptTextarea as HTMLTextAreaElement).value).toContain('Container restarted 3 times');
    expect(screen.queryByText('Reset Prompt')).not.toBeInTheDocument();

    // Now manually edit prompt
    fireEvent.change(promptTextarea, {
      target: { value: 'Manually customized prompt content' },
    });
    expect(screen.getByText('Reset Prompt')).toBeInTheDocument();

    // Further changes to situational context do NOT overwrite manual edits
    fireEvent.change(contextTextarea, {
      target: { value: 'Updated context while prompt is custom' },
    });
    expect((promptTextarea as HTMLTextAreaElement).value).toBe('Manually customized prompt content');
    expect(screen.getByText('Reset Prompt')).toBeInTheDocument();

    // Clicking Reset Prompt restores default prompt with current context and hides button
    fireEvent.click(screen.getByText('Reset Prompt'));
    expect(screen.queryByText('Reset Prompt')).not.toBeInTheDocument();
    expect((promptTextarea as HTMLTextAreaElement).value).toContain('Updated context while prompt is custom');
  });
});

