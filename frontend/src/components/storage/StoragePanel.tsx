import React, { useEffect, useState } from 'react';
import {
  Database,
  AlertCircle,
  RefreshCw,
  Trash2,
  History,
  Brain,
  Copy,
  Check,
} from 'lucide-react';
import { StorageMetricsResponse, AiAuditEntry } from '../../types.ts';
import { fetchSettings, updateSettings } from '../../api/settings.ts';
import { fetchStorageMetrics } from '../../api/system.ts';
import { fetchAiAudit, deleteAiAuditItem, clearAiAuditLog } from '../../api/ai.ts';
import { useClipboard, useMediaQuery } from '../../utils/hooks.ts';
import { extractCleanSummary } from '../../utils/summary.ts';
import { DEFAULT_SYSTEM_PROMPT, buildFullEnvelope } from '../../utils/aiPrompt.ts';
import { Modal } from '../common/Modal.tsx';
import { MarkdownRenderer } from '../common/MarkdownRenderer.tsx';
import { StorageCard } from '../settings/StorageCard.tsx';
import { RetentionSlider } from '../settings/RetentionSlider.tsx';
import { StorageTrendChart } from '../settings/StorageTrendChart.tsx';

export const StoragePanel: React.FC = () => {
  const [storageMetrics, setStorageMetrics] = useState<StorageMetricsResponse | null>(null);
  const [retentionDays, setRetentionDays] = useState<number>(14);
  const [maxRetentionDays, setMaxRetentionDays] = useState<number | undefined>(undefined);
  const [auditLogs, setAuditLogs] = useState<AiAuditEntry[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const [selectedAuditItem, setSelectedAuditItem] = useState<AiAuditEntry | null>(null);
  const { copied: copiedAuditPrompt, copy: copyAuditPrompt } = useClipboard();
  const [showPromptDetails, setShowPromptDetails] = useState<boolean>(false);
  const [auditPromptViewMode, setAuditPromptViewMode] = useState<'analysis' | 'full'>('analysis');
  const [isDeletingAuditId, setIsDeletingAuditId] = useState<number | null>(null);
  const [showClearAllAuditModal, setShowClearAllAuditModal] = useState<boolean>(false);
  const [isClearingAllAudit, setIsClearingAllAudit] = useState<boolean>(false);
  const [auditError, setAuditError] = useState<string | null>(null);

  const loadAllData = async () => {
    try {
      setIsLoading(true);
      setErrorMsg(null);

      const [settRes, storRes, auditRes] = await Promise.all([
        fetchSettings(),
        fetchStorageMetrics(),
        fetchAiAudit(20, 0).catch(() => ({ items: [], total: 0 })),
      ]);

      setRetentionDays(settRes.retention_days);
      setMaxRetentionDays(settRes.max_retention_days);
      setStorageMetrics(storRes);
      setAuditLogs(auditRes.items);
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to load storage and audit data.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadAllData();
  }, []);

  const handleSaveRetention = async (newDays: number) => {
    await updateSettings({ retention_days: newDays });
    setRetentionDays(newDays);
  };

  const handleCopyAuditPrompt = async () => {
    if (selectedAuditItem?.prompt_sent) {
      const textToCopy =
        auditPromptViewMode === 'full'
          ? buildFullEnvelope(selectedAuditItem.system_prompt || DEFAULT_SYSTEM_PROMPT, selectedAuditItem.prompt_sent)
          : selectedAuditItem.prompt_sent;
      await copyAuditPrompt(textToCopy);
    }
  };

  const handleDeleteAuditItem = async (id: number, e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    try {
      setAuditError(null);
      setIsDeletingAuditId(id);
      await deleteAiAuditItem(id);
      setAuditLogs((prev) => prev.filter((item) => item.id !== id));
      if (selectedAuditItem && selectedAuditItem.id === id) {
        setSelectedAuditItem(null);
      }
    } catch (err: any) {
      setAuditError(`Failed to delete AI analysis: ${err.message || err}`);
    } finally {
      setIsDeletingAuditId(null);
    }
  };

  const handleClearAllAudit = async () => {
    try {
      setAuditError(null);
      setIsClearingAllAudit(true);
      await clearAiAuditLog();
      setAuditLogs([]);
      setSelectedAuditItem(null);
      setShowClearAllAuditModal(false);
    } catch (err: any) {
      setAuditError(`Failed to clear AI audit log: ${err.message || err}`);
    } finally {
      setIsClearingAllAudit(false);
    }
  };

  const isMobile = useMediaQuery('(max-width: 767px)');

  if (isLoading && !storageMetrics) {
    return (
      <div className="p-8 text-center text-slate-500 font-mono text-xs flex items-center justify-center gap-2">
        <RefreshCw className="w-4 h-4 animate-spin text-accent-500" />
        <span>Loading storage metrics and audit history...</span>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto p-3 sm:p-6 space-y-6 sm:space-y-8">
      {/* Header */}
      <div>
        <h2 className="text-base font-bold text-slate-100 flex items-center gap-2">
          <Database className="w-5 h-5 text-accent-500" />
          <span>Storage, Retention & Audit History</span>
        </h2>
        <p className="text-xs text-slate-400 mt-0.5">
          Monitor SQLite database growth, configure log retention policies, and review historical AI root-cause analyses.
        </p>
      </div>

      {errorMsg && (
        <div className="p-3 bg-red-950/60 border border-red-800 rounded-lg flex items-start gap-2 text-xs text-red-300">
          <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
          <span>{errorMsg}</span>
        </div>
      )}

      {/* Storage & Retention Section */}
      <section className="space-y-4">
        <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
          Storage & Retention
        </h3>
        <StorageCard metrics={storageMetrics} />
        {retentionDays !== undefined && (
          <RetentionSlider
            retentionDays={retentionDays}
            maxRetentionDays={maxRetentionDays}
            onSaveRetention={handleSaveRetention}
            onPruneCompleted={loadAllData}
          />
        )}

        <StorageTrendChart history={storageMetrics?.history || []} />
      </section>

      {/* AI Audit Log History Section */}
      <section className="bg-dark-900 border border-dark-700 rounded-xl overflow-hidden shadow-md">
        <div className="p-4 bg-dark-950 border-b border-dark-700 flex items-center justify-between">
          <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
            <History className="w-4 h-4 text-accent-500" />
            <span>AI Root-Cause Audit Log ({auditLogs.length})</span>
          </h3>
          {auditLogs.length > 0 && (
            <button
              type="button"
              onClick={() => setShowClearAllAuditModal(true)}
              className="flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-medium bg-red-950/40 hover:bg-red-900/60 border border-red-800/80 text-red-300 rounded transition cursor-pointer"
              title="Delete all historical AI root-cause analyses"
            >
              <Trash2 className="w-3.5 h-3.5" />
              <span>Clear All</span>
            </button>
          )}
        </div>

        {auditError && (
          <div className="p-3 bg-red-950/60 border-b border-red-800 flex items-start gap-2 text-xs text-red-300">
            <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
            <span className="flex-1">{auditError}</span>
            <button
              type="button"
              onClick={() => setAuditError(null)}
              className="text-red-400 hover:text-red-200 text-xs ml-auto cursor-pointer"
            >
              ✕
            </button>
          </div>
        )}

        {auditLogs.length === 0 ? (
          <div className="p-6 text-center text-slate-500 font-mono text-xs">
            No AI analyses executed yet. Select logs in the stream and click "Explain with AI".
          </div>
        ) : isMobile ? (
          /* Mobile Card View */
          <div className="divide-y divide-dark-800">
            {auditLogs.map((item) => (
              <div
                key={item.id}
                onClick={() => {
                  setSelectedAuditItem(item);
                  setShowPromptDetails(false);
                  setAuditPromptViewMode('analysis');
                }}
                className="p-3.5 space-y-2 hover:bg-dark-800/40 transition cursor-pointer select-none"
              >
                {/* Line 1: Host • App & Timestamp */}
                <div className="flex items-center justify-between text-xs gap-2">
                  <div className="truncate font-mono">
                    <span className="font-semibold text-accent-400">{item.source_alias}</span>
                    <span className="text-slate-500"> • {item.app_name}</span>
                  </div>
                  <span className="text-[10px] text-slate-500 font-mono shrink-0">
                    {item.timestamp.slice(0, 16).replace('T', ' ')}
                  </span>
                </div>

                {/* Line 2: Clean Summary */}
                <div
                  className="text-slate-200 font-sans text-xs line-clamp-2 leading-relaxed"
                  title={extractCleanSummary(item.response_text)}
                >
                  {extractCleanSummary(item.response_text)}
                </div>

                {/* Line 3: Model + Tokens + Actions */}
                <div className="flex items-center justify-between pt-1 text-[11px] text-slate-400 font-mono">
                  <div className="flex items-center gap-1.5 truncate">
                    <span className="bg-dark-950 border border-dark-700 px-1.5 py-0.5 rounded text-[10px] text-slate-300 truncate max-w-[130px]">
                      {item.model}
                    </span>
                    <span className="text-slate-500 text-[10px]">
                      {item.tokens_used.toLocaleString()} tok
                    </span>
                  </div>
                  <div className="flex items-center gap-2 font-sans shrink-0" onClick={(e) => e.stopPropagation()}>
                    <button
                      type="button"
                      onClick={() => {
                        setSelectedAuditItem(item);
                        setShowPromptDetails(false);
                        setAuditPromptViewMode('analysis');
                      }}
                      className="px-2.5 py-1 text-xs font-mono text-accent-400 bg-accent-950/50 hover:bg-accent-900/60 border border-accent-800/80 rounded transition cursor-pointer"
                      title="View analysis details"
                    >
                      View
                    </button>
                    <button
                      type="button"
                      disabled={isDeletingAuditId === item.id}
                      onClick={(e) => handleDeleteAuditItem(item.id, e)}
                      className="p-1.5 text-slate-400 hover:text-red-400 hover:bg-red-950/50 rounded border border-transparent hover:border-red-900/50 transition cursor-pointer disabled:opacity-50"
                      title="Delete this analysis"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          /* Desktop Table View */
          <div className="divide-y divide-dark-800 font-mono text-xs">
            <div className="grid grid-cols-[135px_150px_130px_75px_1fr_95px] px-4 py-2 text-slate-400 font-semibold text-[11px] bg-dark-950/60 select-none">
              <div>TIMESTAMP</div>
              <div>HOST • APP</div>
              <div>MODEL</div>
              <div>TOKENS</div>
              <div>SUMMARY</div>
              <div className="text-right">ACTIONS</div>
            </div>

            {auditLogs.map((item) => (
              <div
                key={item.id}
                onClick={() => {
                  setSelectedAuditItem(item);
                  setShowPromptDetails(false);
                  setAuditPromptViewMode('analysis');
                }}
                className="grid grid-cols-[135px_150px_130px_75px_1fr_95px] px-4 py-2.5 items-center hover:bg-dark-800 transition text-[11px] cursor-pointer group select-none"
              >
                <div className="text-slate-400 group-hover:text-slate-300">
                  {item.timestamp.slice(0, 19).replace('T', ' ')}
                </div>
                <div className="text-slate-300 truncate pr-2">
                  <span className="font-semibold text-accent-400">{item.source_alias}</span>
                  <span className="text-slate-500"> • {item.app_name}</span>
                </div>
                <div className="text-slate-400 truncate">{item.model}</div>
                <div className="text-slate-300">{item.tokens_used.toLocaleString()}</div>
                <div
                  className="text-slate-200 font-sans text-xs truncate pr-2 group-hover:text-white"
                  title={extractCleanSummary(item.response_text)}
                >
                  {extractCleanSummary(item.response_text)}
                </div>
                <div className="flex items-center justify-end gap-1.5" onClick={(e) => e.stopPropagation()}>
                  <button
                    type="button"
                    onClick={() => {
                      setSelectedAuditItem(item);
                      setShowPromptDetails(false);
                      setAuditPromptViewMode('analysis');
                    }}
                    className="px-2 py-0.5 text-[11px] font-mono text-accent-400 bg-accent-950/50 hover:bg-accent-900/60 border border-accent-800/80 rounded transition cursor-pointer"
                    title="View analysis details"
                  >
                    View
                  </button>
                  <button
                    type="button"
                    disabled={isDeletingAuditId === item.id}
                    onClick={(e) => handleDeleteAuditItem(item.id, e)}
                    className="p-1 text-slate-400 hover:text-red-400 hover:bg-red-950/50 rounded border border-transparent hover:border-red-900/50 transition cursor-pointer disabled:opacity-50"
                    title="Delete this analysis"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Historical AI Analysis Detail Modal */}
      {selectedAuditItem && (
        <Modal
          isOpen={!!selectedAuditItem}
          onClose={() => {
            setSelectedAuditItem(null);
            setAuditError(null);
          }}
          title="Historical AI Root-Cause Analysis"
          maxWidth="max-w-3xl"
        >
          <div className="space-y-4 text-xs font-sans">
            {auditError && (
              <div className="p-3 bg-red-950/60 border border-red-800 rounded-lg flex items-start gap-2 text-xs text-red-300">
                <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
                <span className="flex-1">{auditError}</span>
                <button
                  type="button"
                  onClick={() => setAuditError(null)}
                  className="text-red-400 hover:text-red-200 text-xs ml-auto cursor-pointer"
                >
                  ✕
                </button>
              </div>
            )}
            {/* Header info - 2-row layout */}
            <div className="bg-dark-950 p-3.5 rounded-lg border border-dark-700 space-y-2.5">
              {/* Row 1: Source / App Name + Timestamp */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Brain className="w-4 h-4 text-accent-400" />
                  <span className="font-semibold text-slate-100 font-mono text-xs">
                    {selectedAuditItem.source_alias} • {selectedAuditItem.app_name}
                  </span>
                  <span className="text-slate-400 text-[11px]">
                    ({selectedAuditItem.log_count} log{selectedAuditItem.log_count === 1 ? '' : 's'})
                  </span>
                </div>
                <span className="font-mono text-[11px] text-slate-400">
                  {selectedAuditItem.timestamp.slice(0, 19).replace('T', ' ')}
                </span>
              </div>

              {/* Row 2: Model Badge + Token Breakdown Pills */}
              <div className="flex flex-wrap items-center justify-between pt-2 border-t border-dark-800 text-[11px] font-mono gap-2">
                <div className="flex items-center gap-1.5 bg-dark-900 border border-dark-700 px-2 py-0.5 rounded text-slate-300">
                  <span className="text-slate-400 text-[10px] uppercase font-semibold">Model:</span>
                  <span className="text-accent-400 font-medium">{selectedAuditItem.model}</span>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-slate-300">
                    Total: <span className="text-slate-100 font-semibold">{selectedAuditItem.tokens_used.toLocaleString()}</span>
                  </span>
                  {selectedAuditItem.tokens_in !== undefined && (
                    <span className="bg-emerald-950/60 border border-emerald-800/80 text-emerald-300 px-1.5 py-0.5 rounded">
                      ↓ {selectedAuditItem.tokens_in.toLocaleString()} in
                    </span>
                  )}
                  {selectedAuditItem.tokens_out !== undefined && (
                    <span className="bg-sky-950/60 border border-sky-800/80 text-sky-300 px-1.5 py-0.5 rounded">
                      ↑ {selectedAuditItem.tokens_out.toLocaleString()} out
                    </span>
                  )}
                  {Boolean(selectedAuditItem.tokens_thoughts) && (
                    <span className="bg-purple-950/60 border border-purple-800/80 text-purple-300 px-1.5 py-0.5 rounded">
                      ⚡ {selectedAuditItem.tokens_thoughts!.toLocaleString()} thinking
                    </span>
                  )}
                </div>
              </div>
            </div>

            {/* Situational Context note if present */}
            {selectedAuditItem.user_context && (
              <div className="bg-dark-950 p-3 rounded-lg border border-dark-700">
                <h4 className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1">
                  Situational Context from Operator
                </h4>
                <p className="text-slate-200 text-xs leading-relaxed">
                  {selectedAuditItem.user_context}
                </p>
              </div>
            )}

            {/* Rendered Full Response with Markdown */}
            <div className="bg-dark-950 p-4 rounded-lg border border-dark-700">
              <h4 className="text-[11px] font-semibold text-accent-400 uppercase tracking-wider mb-2">
                AI Diagnosis & Remediation
              </h4>
              <MarkdownRenderer content={selectedAuditItem.response_text} />
            </div>

            {/* Collapsible Redacted Prompt / Logs */}
            <div className="border border-dark-700 rounded-lg overflow-hidden bg-dark-950">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between p-2.5 sm:px-3 sm:py-2 bg-dark-900 border-b border-dark-700 gap-2">
                <button
                  type="button"
                  onClick={() => setShowPromptDetails(!showPromptDetails)}
                  className="text-[11px] font-semibold text-slate-300 uppercase tracking-wider hover:text-slate-100 transition cursor-pointer text-left"
                >
                  {showPromptDetails ? '▼ Hide Submitted Logs & Prompt' : '▶ View Submitted Logs & Prompt'}
                </button>
                {showPromptDetails && (
                  <div className="flex flex-wrap items-center justify-between sm:justify-end gap-2">
                    {/* View mode toggle pill */}
                    <div className="flex items-center bg-dark-950 border border-dark-700 rounded p-0.5 text-[10px] font-mono">
                      <button
                        type="button"
                        onClick={() => setAuditPromptViewMode('analysis')}
                        className={`px-2 py-0.5 rounded transition cursor-pointer ${
                          auditPromptViewMode === 'analysis'
                            ? 'bg-accent-600 text-white font-medium shadow-xs'
                            : 'text-slate-400 hover:text-slate-200'
                        }`}
                      >
                        Analysis Prompt
                      </button>
                      <button
                        type="button"
                        onClick={() => setAuditPromptViewMode('full')}
                        className={`px-2 py-0.5 rounded transition cursor-pointer ${
                          auditPromptViewMode === 'full'
                            ? 'bg-accent-600 text-white font-medium shadow-xs'
                            : 'text-slate-400 hover:text-slate-200'
                        }`}
                      >
                        Full LLM Prompt
                      </button>
                    </div>
                    <button
                      type="button"
                      onClick={handleCopyAuditPrompt}
                      className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-slate-200 transition cursor-pointer shrink-0"
                    >
                      {copiedAuditPrompt ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                      <span>{copiedAuditPrompt ? 'Copied' : 'Copy Prompt'}</span>
                    </button>
                  </div>
                )}
              </div>
              {showPromptDetails && (
                <pre className="p-3 overflow-x-auto text-xs font-mono text-slate-300 max-h-56 overflow-y-auto leading-relaxed select-text whitespace-pre-wrap">
                  <code>
                    {auditPromptViewMode === 'full'
                      ? buildFullEnvelope(selectedAuditItem.system_prompt || DEFAULT_SYSTEM_PROMPT, selectedAuditItem.prompt_sent)
                      : selectedAuditItem.prompt_sent}
                  </code>
                </pre>
              )}
            </div>

            {/* AI Advisory Disclaimer */}
            <div className="p-3 bg-dark-950/80 border border-dark-700/80 rounded-lg flex items-start gap-2.5 text-slate-400 text-[11px] leading-relaxed">
              <AlertCircle className="w-4 h-4 text-amber-400/90 shrink-0 mt-0.5" />
              <span>
                AI root-cause analyses and remediation commands are advisory only. Always verify proposed commands and configurations before executing on systems. API calls consume tokens billed to your provider.
              </span>
            </div>

            {/* Footer Buttons */}
            <div className="flex items-center justify-between pt-2 border-t border-dark-800">
              <button
                type="button"
                disabled={isDeletingAuditId === selectedAuditItem.id}
                onClick={() => handleDeleteAuditItem(selectedAuditItem.id)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-red-400 hover:text-red-300 bg-red-950/40 hover:bg-red-900/60 border border-red-800/80 rounded-lg transition cursor-pointer disabled:opacity-50"
              >
                <Trash2 className="w-3.5 h-3.5" />
                <span>Delete Analysis</span>
              </button>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setSelectedAuditItem(null)}
                  className="px-3.5 py-2 text-xs bg-dark-700 hover:bg-dark-600 text-slate-200 rounded-lg transition cursor-pointer"
                >
                  Close
                </button>
              </div>
            </div>
          </div>
        </Modal>
      )}

      {/* Clear All Confirmation Modal */}
      {showClearAllAuditModal && (
        <Modal
          isOpen={showClearAllAuditModal}
          onClose={() => {
            setShowClearAllAuditModal(false);
            setAuditError(null);
          }}
          title="Clear AI Audit Log"
          maxWidth="max-w-md"
        >
          <div className="space-y-4 text-xs font-sans">
            {auditError && (
              <div className="p-3 bg-red-950/60 border border-red-800 rounded-lg flex items-start gap-2 text-xs text-red-300">
                <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
                <span className="flex-1">{auditError}</span>
              </div>
            )}
            <div className="flex items-start gap-3 p-3 bg-red-950/30 border border-red-800/60 rounded-lg text-slate-200">
              <AlertCircle className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
              <div>
                <p className="font-semibold text-slate-100 text-xs mb-1">
                  Permanently delete all historical AI analyses?
                </p>
                <p className="text-slate-400 leading-relaxed">
                  This will remove all {auditLogs.length} saved diagnoses, operator context notes, and submitted prompt histories. This action cannot be undone.
                </p>
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setShowClearAllAuditModal(false)}
                className="px-3 py-1.5 text-xs bg-dark-800 hover:bg-dark-700 text-slate-300 border border-dark-600 rounded-lg transition cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={isClearingAllAudit}
                onClick={handleClearAllAudit}
                className="flex items-center gap-1.5 px-3.5 py-1.5 text-xs bg-red-600 hover:bg-red-500 text-white font-medium rounded-lg shadow transition cursor-pointer disabled:opacity-50"
              >
                <Trash2 className="w-3.5 h-3.5" />
                <span>{isClearingAllAudit ? 'Clearing...' : 'Delete All'}</span>
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
};
