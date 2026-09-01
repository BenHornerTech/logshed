import React, { useEffect, useState } from 'react';
import { Sparkles, Send, Copy, Check, Shield, RefreshCw, AlertCircle } from 'lucide-react';
import { LogEntry, AiPreviewResponse, AiAnalysisResponse } from '../../types.ts';
import { previewAiPrompt, analyzeLogs } from '../../api/ai.ts';
import { sendPushoverNotification } from '../../api/notifications.ts';
import { Modal } from '../common/Modal.tsx';

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

  const [userContext, setUserContext] = useState<string>('');
  const [provider, setProvider] = useState<string>('gemini');
  const [model, setModel] = useState<string>('gemini-2.5-flash');

  const [isAnalyzing, setIsAnalyzing] = useState<boolean>(false);
  const [analysisResult, setAnalysisResult] = useState<AiAnalysisResponse | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

  const [isSendingPushover, setIsSendingPushover] = useState<boolean>(false);
  const [pushoverStatus, setPushoverStatus] = useState<string | null>(null);
  const [copiedPrompt, setCopiedPrompt] = useState<boolean>(false);

  useEffect(() => {
    if (isOpen && selectedLogs.length > 0) {
      loadPreview();
    } else {
      setPreview(null);
      setAnalysisResult(null);
      setPreviewError(null);
      setAnalysisError(null);
      setUserContext('');
      setPushoverStatus(null);
    }
  }, [isOpen, selectedLogs]);

  const loadPreview = async () => {
    const validLogIds = selectedLogs
      .map((l) => l.id)
      .filter((id): id is number => typeof id === 'number' && !isNaN(id));

    if (validLogIds.length === 0) {
      setPreviewError('No valid log IDs selected for AI analysis.');
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
    } catch (err: any) {
      setPreviewError(err.message || 'Failed to generate sanitized AI preview.');
    } finally {
      setIsLoadingPreview(false);
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

    try {
      setIsAnalyzing(true);
      setAnalysisError(null);
      const res = await analyzeLogs({
        log_ids: validLogIds,
        user_context: userContext.trim() || undefined,
        provider,
        model,
      });
      setAnalysisResult(res);
    } catch (err: any) {
      setAnalysisError(err.message || 'AI analysis request failed.');
    } finally {
      setIsAnalyzing(false);
    }
  };

  const handleSendToPushover = async () => {
    if (!analysisResult || !preview) return;
    try {
      setIsSendingPushover(true);
      setPushoverStatus(null);
      const title = `[Log Hub] ${preview.source_alias}: ${preview.app_name}`;
      const message = `${analysisResult.summary}\n\nRoot Cause:\n${analysisResult.root_cause}\n\nRemediation:\n${analysisResult.remediation}`;
      await sendPushoverNotification({
        title,
        message,
        priority: 0,
      });
      setPushoverStatus('Notification sent to Pushover successfully!');
      setTimeout(() => setPushoverStatus(null), 4000);
    } catch (err: any) {
      setPushoverStatus(`Failed to send notification: ${err.message}`);
    } finally {
      setIsSendingPushover(false);
    }
  };

  const handleCopyPrompt = () => {
    if (preview) {
      navigator.clipboard.writeText(preview.sanitized_prompt);
      setCopiedPrompt(true);
      setTimeout(() => setCopiedPrompt(false), 2000);
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
            <Shield className="w-4 h-4 text-emerald-400" />
            <span className="font-semibold text-slate-200">
              {selectedLogs.length} Log{selectedLogs.length === 1 ? '' : 's'} Selected
            </span>
            {preview && (
              <span className="text-slate-400 font-mono">
                ({preview.source_alias} • {preview.app_name})
              </span>
            )}
          </div>

          {preview && (
            <div className="flex items-center gap-2 font-mono text-[11px] text-slate-400">
              <span className="px-2 py-0.5 bg-dark-800 rounded border border-dark-700">
                ~{preview.estimated_tokens} tokens
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
            {/* Sanitized Preview Block */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[11px] font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-1">
                  <Shield className="w-3.5 h-3.5 text-emerald-400" />
                  <span>Sanitized Prompt Preview (Secrets Scrubbed)</span>
                </span>
                <button
                  onClick={handleCopyPrompt}
                  className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-slate-200 transition"
                >
                  {copiedPrompt ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                  <span>{copiedPrompt ? 'Copied' : 'Copy Prompt'}</span>
                </button>
              </div>
              <div className="bg-dark-950 border border-dark-700 rounded-lg p-3 font-mono text-slate-300 text-xs whitespace-pre-wrap max-h-48 overflow-y-auto select-text">
                {preview.sanitized_prompt}
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
                  placeholder="gemini-2.5-flash / gpt-4o / llama3.2"
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
                onChange={(e) => setUserContext(e.target.value)}
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

            {/* Run Analysis Action */}
            <div className="pt-2 flex justify-end">
              <button
                onClick={handleRunAnalysis}
                disabled={isAnalyzing}
                className="bg-accent-600 hover:bg-accent-500 disabled:opacity-50 text-white font-medium px-4 py-2 rounded-lg text-xs flex items-center gap-2 transition shadow-md"
              >
                {isAnalyzing ? (
                  <>
                    <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                    <span>Analyzing Logs with LLM...</span>
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
            {/* Header info */}
            <div className="flex items-center justify-between text-slate-400 font-mono text-[11px]">
              <span>Model: <span className="text-slate-200">{analysisResult.model_used}</span></span>
              <span>Tokens Used: <span className="text-slate-200">{analysisResult.tokens_used}</span></span>
            </div>

            {/* Summary */}
            <div className="bg-dark-950 p-3 rounded-lg border border-dark-700">
              <h4 className="text-[11px] font-semibold text-accent-400 uppercase tracking-wider mb-1">
                Summary
              </h4>
              <p className="text-slate-200 text-xs leading-relaxed">{analysisResult.summary}</p>
            </div>

            {/* Root Cause */}
            <div className="bg-dark-950 p-3 rounded-lg border border-dark-700">
              <h4 className="text-[11px] font-semibold text-amber-400 uppercase tracking-wider mb-1">
                Root Cause Analysis
              </h4>
              <p className="text-slate-200 text-xs leading-relaxed whitespace-pre-wrap">
                {analysisResult.root_cause}
              </p>
            </div>

            {/* Actionable Remediation */}
            <div className="bg-dark-950 p-3 rounded-lg border border-dark-700">
              <h4 className="text-[11px] font-semibold text-emerald-400 uppercase tracking-wider mb-1">
                Actionable Remediation
              </h4>
              <div className="text-slate-200 text-xs leading-relaxed font-mono whitespace-pre-wrap">
                {analysisResult.remediation}
              </div>
            </div>

            {pushoverStatus && (
              <div className="p-2.5 bg-dark-800 border border-dark-700 rounded text-xs text-slate-300">
                {pushoverStatus}
              </div>
            )}

            {/* Action Buttons */}
            <div className="flex items-center justify-between pt-2">
              <button
                onClick={() => setAnalysisResult(null)}
                className="text-xs text-slate-400 hover:text-slate-200 underline"
              >
                Back to Preview & Edit
              </button>

              <button
                onClick={handleSendToPushover}
                disabled={isSendingPushover}
                className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white font-medium px-4 py-2 rounded-lg text-xs flex items-center gap-2 transition shadow-md"
              >
                <Send className="w-3.5 h-3.5" />
                <span>{isSendingPushover ? 'Sending...' : 'Send to Pushover'}</span>
              </button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
};
