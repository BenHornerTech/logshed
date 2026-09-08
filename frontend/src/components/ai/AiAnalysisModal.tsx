import React, { useEffect, useState, useRef, useMemo } from 'react';
import { Sparkles, Copy, Check, Shield, RefreshCw, AlertCircle, Info, RotateCcw } from 'lucide-react';
import { LogEntry, AiPreviewResponse, AiDiagnosisResponse } from '../../types.ts';
import { previewAiPrompt, diagnoseLogs } from '../../api/ai.ts';
import { useClipboard } from '../../utils/hooks.ts';
import { DEFAULT_AI_MODEL, DEFAULT_SYSTEM_PROMPT, buildFullEnvelope, parseFullEnvelope, normalizePrompt } from '../../utils/aiPrompt.ts';
import { Modal } from '../common/Modal.tsx';
import { MarkdownRenderer } from '../common/MarkdownRenderer.tsx';

interface AiAnalysisModalProps {
  isOpen: boolean;
  onClose: () => void;
  selectedLogs: LogEntry[];
}

export const AiAnalysisModal: React.FC<AiAnalysisModalProps> = ({
  isOpen,
  onClose,
  selectedLogs,
}) => {
  const [preview, setPreview] = useState<AiPreviewResponse | null>(null);
  const [isLoadingPreview, setIsLoadingPreview] = useState<boolean>(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [promptText, setPromptText] = useState<string>('');
  const [systemPrompt, setSystemPrompt] = useState<string>(DEFAULT_SYSTEM_PROMPT);
  const [promptViewMode, setPromptViewMode] = useState<'analysis' | 'full'>('analysis');

  const [userContext, setUserContext] = useState<string>('');
  const [provider, setProvider] = useState<string>('gemini');
  const [model, setModel] = useState<string>(DEFAULT_AI_MODEL);

  const [isDiagnosing, setIsDiagnosing] = useState<boolean>(false);
  const [analysisResult, setAnalysisResult] = useState<AiDiagnosisResponse | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

  const { copied: copiedPrompt, copy: copyPrompt } = useClipboard();
  const promptTextareaRef = useRef<HTMLTextAreaElement | null>(null);

  const buildCombinedPrompt = (basePrompt: string, ctx: string) => {
    if (!ctx.trim()) return basePrompt;
    const parts = basePrompt.split('### Redacted Log Stream');
    if (parts.length === 2) {
      return `${parts[0]}### Situational Context from Operator\n${ctx.trim()}\n\n### Redacted Log Stream${parts[1]}`;
    }
    return `${basePrompt}\n\n### Situational Context from Operator\n${ctx.trim()}`;
  };

  const defaultPrompt = useMemo(() => {
    if (!preview) return '';
    return buildCombinedPrompt(preview.redacted_prompt, userContext);
  }, [preview, userContext]);

  const hasEditedPrompt = Boolean(
    preview && normalizePrompt(promptText) !== normalizePrompt(defaultPrompt)
  );
  const hasEditedSystem = Boolean(
    preview &&
      normalizePrompt(systemPrompt) !==
        normalizePrompt(preview.system_prompt || DEFAULT_SYSTEM_PROMPT)
  );
  const isModified = hasEditedPrompt || hasEditedSystem;

  useEffect(() => {
    if (isOpen && selectedLogs.length > 0) {
      loadPreview();
    } else {
      setPreview(null);
      setAnalysisResult(null);
      setPreviewError(null);
      setAnalysisError(null);
      setUserContext('');
      setPromptText('');
      setSystemPrompt(DEFAULT_SYSTEM_PROMPT);
      setPromptViewMode('analysis');
    }
  }, [isOpen, selectedLogs]);

  // Auto-expand prompt textarea to avoid premature CSS height clipping
  useEffect(() => {
    if (promptTextareaRef.current) {
      promptTextareaRef.current.style.height = 'auto';
      promptTextareaRef.current.style.height = `${Math.max(160, promptTextareaRef.current.scrollHeight)}px`;
    }
  }, [promptText, systemPrompt, promptViewMode, preview]);

  const loadPreview = async () => {
    const validLogIds = selectedLogs
      .map((l) => l.id)
      .filter((id): id is number => typeof id === 'number' && !isNaN(id));

    if (validLogIds.length === 0) {
      setPreviewError('No valid log IDs selected for AI analysis.');
      return;
    }

    if (validLogIds.length > 200) {
      setPreviewError(
        `Cannot analyze more than 200 logs at once (${validLogIds.length} logs selected). Please reduce your selection to 200 logs or fewer.`
      );
      return;
    }

    try {
      setIsLoadingPreview(true);
      setPreviewError(null);
      const res = await previewAiPrompt({
        log_ids: validLogIds,
      });
      setPreview(res);
      setProvider(res.provider);
      setModel(res.model);
      const initialSys = res.system_prompt || DEFAULT_SYSTEM_PROMPT;
      setSystemPrompt(initialSys);
      setPromptText(buildCombinedPrompt(res.redacted_prompt, userContext));
    } catch (err: any) {
      setPreviewError(err.message || 'Failed to generate redacted AI preview.');
    } finally {
      setIsLoadingPreview(false);
    }
  };

  const handleUserContextChange = (newContext: string) => {
    const prevDefault = buildCombinedPrompt(preview?.redacted_prompt || '', userContext);
    setUserContext(newContext);
    // If promptText currently matches previous default, keep it in sync with newContext
    if (preview && normalizePrompt(promptText) === normalizePrompt(prevDefault)) {
      setPromptText(buildCombinedPrompt(preview.redacted_prompt, newContext));
    }
  };

  const handlePromptChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    if (promptViewMode === 'full') {
      const parsed = parseFullEnvelope(e.target.value, systemPrompt);
      setSystemPrompt(parsed.systemPrompt);
      setPromptText(parsed.userPrompt);
    } else {
      setPromptText(e.target.value);
    }
  };

  const handleResetPrompt = () => {
    if (preview) {
      setPromptText(defaultPrompt);
      setSystemPrompt(preview.system_prompt || DEFAULT_SYSTEM_PROMPT);
    }
  };

  const handleRunAnalysis = async () => {
    const validLogIds = selectedLogs
      .map((l) => l.id)
      .filter((id): id is number => typeof id === 'number' && !isNaN(id));

    if (validLogIds.length === 0) {
      setAnalysisError('No valid log IDs selected for AI analysis.');
      return;
    }

    if (validLogIds.length > 200) {
      setAnalysisError(
        `Cannot analyze more than 200 logs at once (${validLogIds.length} logs selected). Please reduce your selection to 200 logs or fewer.`
      );
      return;
    }

    try {
      setIsDiagnosing(true);
      setAnalysisError(null);
      const res = await diagnoseLogs({
        log_ids: validLogIds,
        user_context: userContext.trim() || undefined,
        prompt_override: hasEditedPrompt ? promptText.trim() : undefined,
        system_prompt_override: hasEditedSystem ? systemPrompt.trim() : undefined,
        provider,
        model,
      });
      setAnalysisResult(res);
    } catch (err: any) {
      setAnalysisError(err.message || 'AI diagnosis request failed.');
    } finally {
      setIsDiagnosing(false);
    }
  };

  const estimatedTokens = useMemo(() => {
    if (!promptText) {
      return preview ? preview.estimated_tokens : 0;
    }
    const currentSysLen = systemPrompt.length;
    return Math.max(1, Math.floor(promptText.length / 3.5) + Math.floor(currentSysLen / 3.5) + 50);
  }, [promptText, systemPrompt, preview]);

  const handleCopyPrompt = async () => {
    const textToCopy =
      promptViewMode === 'full'
        ? buildFullEnvelope(systemPrompt, promptText)
        : promptText;
    if (textToCopy) {
      await copyPrompt(textToCopy);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="On-Demand AI Root-Cause Analysis"
      maxWidth="max-w-3xl"
    >
      <div className="space-y-4 text-xs font-sans">
        {/* Host & Target Scope Header */}
        <div className="p-3 bg-dark-950 rounded-lg border border-dark-700 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Shield className={`w-4 h-4 ${selectedLogs.length > 200 ? 'text-amber-400' : 'text-emerald-400'}`} />
            <span className="font-semibold text-slate-200">
              {selectedLogs.length} Log{selectedLogs.length === 1 ? '' : 's'} Selected
            </span>
            {selectedLogs.length > 200 && (
              <span className="text-[11px] text-amber-400 font-mono">
                (Max 200 logs allowed)
              </span>
            )}
            {preview && (
              <span className="text-slate-400 font-mono">
                ({preview.source_alias} • {preview.app_name})
              </span>
            )}
          </div>

          {preview && (
            <div className="flex items-center gap-2 font-mono text-[11px] text-slate-400">
              <span
                className="px-2 py-0.5 bg-dark-800 rounded border border-dark-700 flex items-center gap-1.5 cursor-help"
                title="Estimated prompt/input tokens only (includes system instructions and metadata). Does not include model thinking or response output tokens."
              >
                <span>~{estimatedTokens.toLocaleString()} tokens</span>
                <Info className="w-3.5 h-3.5 text-slate-400 hover:text-slate-200 transition" />
              </span>
            </div>
          )}
        </div>

        {previewError && (
          <div className="p-3 bg-red-950/60 border border-red-800 rounded-lg flex items-start gap-2 text-red-300">
            <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
            <span>{previewError}</span>
          </div>
        )}

        {/* Loading state for preview */}
        {isLoadingPreview && (
          <div className="p-6 text-center text-slate-400 font-mono flex items-center justify-center gap-2">
            <RefreshCw className="w-4 h-4 animate-spin text-accent-400" />
            <span>Scrubbing sensitive tokens & generating AI preview...</span>
          </div>
        )}

        {preview && !analysisResult && (
          <div className="space-y-3">
            {/* Redacted Preview & Prompt Editor Block */}
            <div>
              <div className="flex flex-wrap items-center justify-between mb-1.5 gap-2">
                <div className="flex items-center gap-2">
                  <span className="text-[11px] font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-1">
                    <Shield className="w-3.5 h-3.5 text-emerald-400" />
                    <span>Prompt (Editable)</span>
                  </span>
                  {isModified && (
                    <span className="text-[10px] text-accent-400 font-mono">
                      (modified)
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-2.5">
                  {/* View mode toggle pill */}
                  <div className="flex items-center bg-dark-950 border border-dark-700 rounded p-0.5 text-[10px] font-mono">
                    <button
                      type="button"
                      onClick={() => setPromptViewMode('analysis')}
                      className={`px-2 py-0.5 rounded transition cursor-pointer ${
                        promptViewMode === 'analysis'
                          ? 'bg-accent-600 text-white font-medium shadow-xs'
                          : 'text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      Analysis Prompt
                    </button>
                    <button
                      type="button"
                      onClick={() => setPromptViewMode('full')}
                      className={`px-2 py-0.5 rounded transition cursor-pointer ${
                        promptViewMode === 'full'
                          ? 'bg-accent-600 text-white font-medium shadow-xs'
                          : 'text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      Full LLM Prompt
                    </button>
                  </div>

                  {isModified && (
                    <button
                      type="button"
                      onClick={handleResetPrompt}
                      className="flex items-center gap-1 text-[11px] text-amber-400 hover:text-amber-300 transition cursor-pointer"
                      title="Reset prompt to original generated text"
                      aria-label="Reset prompt to default"
                    >
                      <RotateCcw className="w-3 h-3" />
                      <span>Reset Prompt</span>
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={handleCopyPrompt}
                    className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-slate-200 transition cursor-pointer"
                  >
                    {copiedPrompt ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                    <span>{copiedPrompt ? 'Copied' : 'Copy Prompt'}</span>
                  </button>
                </div>
              </div>
              <textarea
                ref={promptTextareaRef}
                value={promptViewMode === 'full' ? buildFullEnvelope(systemPrompt, promptText) : promptText}
                onChange={handlePromptChange}
                placeholder={promptViewMode === 'full' ? 'Full LLM prompt envelope...' : 'Redacted prompt...'}
                className="w-full bg-dark-950 border border-dark-700 rounded-lg p-3 font-mono text-slate-200 text-xs focus:outline-hidden focus:border-accent-500 leading-relaxed whitespace-pre-wrap resize-y overflow-y-hidden"
              />

              {/* Log Redaction Notice */}
              <div className="mt-2 p-2.5 bg-amber-950/30 border border-amber-800/40 rounded-lg flex items-start gap-2 text-amber-200/90 text-[11px] leading-relaxed">
                <Shield className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                <span>
                  <strong className="font-semibold text-amber-300">Redaction Notice:</strong> Automated credential scrubbing operates on a best-effort basis and may not catch every sensitive token or secret. Please review the prompt above before sending—you are responsible for the contents and sensitive data you transmit to external AI providers.
                </span>
              </div>
            </div>

            {/* Provider & Model Selectors */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                  AI Provider
                </label>
                <select
                  value={provider}
                  onChange={(e) => setProvider(e.target.value)}
                  className="w-full bg-dark-950 border border-dark-700 rounded px-2.5 py-1.5 text-xs text-slate-200 focus:outline-hidden focus:border-accent-500 font-mono"
                >
                  <option value="gemini">Google Gemini</option>
                  <option value="openai">OpenAI</option>
                  <option value="openai_compatible">OpenAI-Compatible (Ollama / LocalAI)</option>
                </select>
              </div>

              <div>
                <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                  Model Identifier
                </label>
                <input
                  type="text"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder={provider === 'gemini' ? DEFAULT_AI_MODEL : provider === 'openai' ? 'gpt-4o' : 'llama3.2'}
                  className="w-full bg-dark-950 border border-dark-700 rounded px-2.5 py-1.5 text-xs text-slate-200 focus:outline-hidden focus:border-accent-500 font-mono"
                />
              </div>
            </div>

            {/* Free-text user context input */}
            <div>
              <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                Optional Situational Context
              </label>
              <textarea
                value={userContext}
                onChange={(e) => handleUserContextChange(e.target.value)}
                placeholder="e.g. Occurred immediately following network switch firmware upgrade, or after container image pull..."
                rows={2}
                className="w-full bg-dark-950 border border-dark-700 rounded-lg p-2.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-sans"
              />
            </div>

            {analysisError && (
              <div className="p-3 bg-red-950/60 border border-red-800 rounded-lg text-red-300">
                {analysisError}
              </div>
            )}

            {/* AI Advisory Disclaimer */}
            <div className="p-3 bg-dark-950/80 border border-dark-700/80 rounded-lg flex items-start gap-2.5 text-slate-400 text-[11px] leading-relaxed">
              <AlertCircle className="w-4 h-4 text-amber-400/90 shrink-0 mt-0.5" />
              <span>
                AI root-cause analyses and remediation commands are advisory only. Always verify proposed commands and configurations before executing on systems. API calls consume tokens billed to your provider.
              </span>
            </div>

            {/* Run Analysis Action */}
            <div className="pt-2 flex justify-end">
              <button
                onClick={handleRunAnalysis}
                disabled={isDiagnosing}
                className="bg-accent-600 hover:bg-accent-500 disabled:opacity-50 text-white font-medium px-4 py-2 rounded-lg text-xs flex items-center gap-2 transition shadow-md"
              >
                {isDiagnosing ? (
                  <>
                    <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                    <span>Running analysis...</span>
                  </>
                ) : (
                  <>
                    <Sparkles className="w-3.5 h-3.5" />
                    <span>Run AI Analysis</span>
                  </>
                )}
              </button>
            </div>
          </div>
        )}

        {/* Structured AI Analysis Result */}
        {analysisResult && (
          <div className="space-y-4 animate-in fade-in">
            {/* Header info - 2-row layout */}
            <div className="bg-dark-950 p-3 rounded-lg border border-dark-700 space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Shield className="w-4 h-4 text-emerald-400" />
                  <span className="font-semibold text-slate-200">
                    {selectedLogs.length} Log{selectedLogs.length === 1 ? '' : 's'} Inspected
                  </span>
                  {preview && (
                    <span className="text-slate-400 font-mono text-[11px]">
                      ({preview.source_alias} • {preview.app_name})
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1.5 font-mono text-[11px] bg-dark-900 border border-dark-700 px-2 py-0.5 rounded text-slate-300">
                  <span className="text-slate-400 text-[10px] uppercase font-semibold">Model:</span>
                  <span className="text-accent-400 font-medium">{analysisResult.model_used}</span>
                </div>
              </div>

              <div className="flex flex-wrap items-center justify-between pt-1.5 border-t border-dark-800 text-[11px] font-mono text-slate-400 gap-2">
                <span>
                  Total Tokens: <span className="text-slate-100 font-semibold">{analysisResult.tokens_used.toLocaleString()}</span>
                </span>
                <div className="flex flex-wrap items-center gap-2 text-[11px]">
                  {analysisResult.tokens_in !== undefined && (
                    <span className="bg-emerald-950/60 border border-emerald-800/80 text-emerald-300 px-1.5 py-0.5 rounded">
                      ↓ {analysisResult.tokens_in.toLocaleString()} in
                    </span>
                  )}
                  {analysisResult.tokens_out !== undefined && (
                    <span className="bg-sky-950/60 border border-sky-800/80 text-sky-300 px-1.5 py-0.5 rounded">
                      ↑ {analysisResult.tokens_out.toLocaleString()} out
                    </span>
                  )}
                  {Boolean(analysisResult.tokens_thoughts) && (
                    <span className="bg-purple-950/60 border border-purple-800/80 text-purple-300 px-1.5 py-0.5 rounded">
                      ⚡ {analysisResult.tokens_thoughts!.toLocaleString()} thinking
                    </span>
                  )}
                </div>
              </div>
            </div>

            {/* Summary */}
            <div className="bg-dark-950 p-3 rounded-lg border border-dark-700">
              <h4 className="text-[11px] font-semibold text-accent-400 uppercase tracking-wider mb-1">
                Summary
              </h4>
              <MarkdownRenderer content={analysisResult.summary} />
            </div>

            {/* Root Cause */}
            <div className="bg-dark-950 p-3 rounded-lg border border-dark-700">
              <h4 className="text-[11px] font-semibold text-amber-400 uppercase tracking-wider mb-1">
                Root Cause Analysis
              </h4>
              <MarkdownRenderer content={analysisResult.root_cause} />
            </div>

            {/* Actionable Remediation */}
            <div className="bg-dark-950 p-3 rounded-lg border border-dark-700">
              <h4 className="text-[11px] font-semibold text-emerald-400 uppercase tracking-wider mb-1">
                Actionable Remediation
              </h4>
              <MarkdownRenderer content={analysisResult.remediation} />
            </div>

            {/* AI Advisory Disclaimer */}
            <div className="p-3 bg-dark-950/80 border border-dark-700/80 rounded-lg flex items-start gap-2.5 text-slate-400 text-[11px] leading-relaxed">
              <AlertCircle className="w-4 h-4 text-amber-400/90 shrink-0 mt-0.5" />
              <span>
                AI root-cause analyses and remediation commands are advisory only. Always verify proposed commands and configurations before executing on systems. API calls consume tokens billed to your provider.
              </span>
            </div>

            {/* Action Buttons */}
            <div className="flex items-center justify-between pt-2">
              <button
                onClick={() => setAnalysisResult(null)}
                className="text-xs text-slate-400 hover:text-slate-200 underline cursor-pointer"
              >
                Back to Preview & Edit
              </button>

              <button
                type="button"
                onClick={onClose}
                className="px-3.5 py-2 text-xs bg-dark-700 hover:bg-dark-600 text-slate-200 rounded-lg transition cursor-pointer"
              >
                Close
              </button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
};
