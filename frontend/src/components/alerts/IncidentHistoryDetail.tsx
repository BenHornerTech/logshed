import React from 'react';
import { Sparkles, Brain, Copy, Check, AlertCircle } from 'lucide-react';
import { AlertHistoryItem } from '../../types.ts';
import { MarkdownRenderer } from '../common/MarkdownRenderer.tsx';
import { useClipboard } from '../../utils/hooks.ts';
import { IncidentStatusBadge } from './IncidentStatusBadge.tsx';

interface IncidentHistoryDetailProps {
  item: AlertHistoryItem;
  channelName?: string;
}

export const IncidentHistoryDetail: React.FC<IncidentHistoryDetailProps> = ({
  item,
  channelName,
}) => {
  const { copied, copy } = useClipboard();

  const isFailed = Boolean(
    item.ai_enrichment &&
      (!item.incident_summary ||
        item.incident_summary.startsWith('AI analysis failed:') ||
        item.incident_summary.startsWith('AI enrichment failed:'))
  );

  return (
    <div className="space-y-3 font-sans text-xs">
      {/* Header info - 2-row layout matching StoragePanel */}
      <div className="bg-dark-950 p-3.5 rounded-lg border border-dark-700 space-y-2.5">
        {/* Row 1: Rule Name & Trigger Counts + Timestamp */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Brain className="w-4 h-4 text-accent-400 shrink-0" />
            <span className="font-semibold text-slate-100 font-mono text-xs">
              {item.rule_name}
            </span>
            <span className="text-slate-400 text-[11px]">
              ({item.trigger_count} matching event{item.trigger_count === 1 ? '' : 's'})
            </span>
          </div>
          <span className="font-mono text-[11px] text-slate-400 shrink-0">
            {new Date(item.triggered_at).toLocaleString()}
          </span>
        </div>

        {/* Row 2: Model Badge, Target Channel & Status Badges */}
        <div className="flex flex-wrap items-center justify-between pt-2 border-t border-dark-800 text-[11px] font-mono gap-2">
          <div className="flex flex-wrap items-center gap-2">
            {item.ai_model && (
              <div className="flex items-center gap-1.5 bg-dark-900 border border-dark-700 px-2 py-0.5 rounded text-slate-300">
                <span className="text-slate-400 text-[10px] uppercase font-semibold">Model:</span>
                <span className="text-accent-400 font-medium">{item.ai_model}</span>
              </div>
            )}
            {channelName && (
              <div className="flex items-center gap-1.5 bg-dark-900 border border-dark-700 px-2 py-0.5 rounded text-slate-300">
                <span className="text-slate-400 text-[10px] uppercase font-semibold">Target:</span>
                <span className="text-slate-200 font-medium">{channelName}</span>
              </div>
            )}
          </div>

          <div className="flex items-center gap-2">
            <IncidentStatusBadge
              aiEnrichment={item.ai_enrichment}
              incidentSummary={item.incident_summary}
            />
          </div>
        </div>
      </div>

      {/* Rendered Full Diagnosis / Incident Summary Card */}
      {item.incident_summary && (
        <div className="bg-dark-950 p-4 rounded-lg border border-dark-700">
          <h4
            className={`text-[11px] font-semibold uppercase tracking-wider mb-2 flex items-center gap-1.5 ${
              isFailed ? 'text-red-400' : 'text-accent-400'
            }`}
          >
            {isFailed ? (
              <>
                <AlertCircle className="w-3.5 h-3.5 text-red-400" />
                Failure Details
              </>
            ) : (
              <>
                <Sparkles className="w-3.5 h-3.5 text-accent-400" />
                AI Incident Diagnosis & Remediation
              </>
            )}
          </h4>

          {isFailed ? (
            <div className="p-3 bg-red-950/30 border border-red-900/60 rounded text-xs text-red-300 leading-relaxed font-mono whitespace-pre-wrap">
              {item.incident_summary}
            </div>
          ) : (
            <MarkdownRenderer content={item.incident_summary} />
          )}
        </div>
      )}

      {/* Triggering Log Snippet Card with Copy Action */}
      {item.sample_log && (
        <div className="border border-dark-700 rounded-lg overflow-hidden bg-dark-950">
          <div className="flex items-center justify-between p-2.5 px-3 bg-dark-900 border-b border-dark-700 gap-2">
            <h4 className="text-[11px] font-semibold text-slate-300 uppercase tracking-wider">
              Triggering Log Snippet
            </h4>
            <button
              type="button"
              onClick={() => copy(item.sample_log || '')}
              className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-slate-200 transition cursor-pointer shrink-0"
              title="Copy triggering log"
            >
              {copied ? (
                <Check className="w-3 h-3 text-emerald-400" />
              ) : (
                <Copy className="w-3 h-3" />
              )}
              <span>{copied ? 'Copied' : 'Copy Log'}</span>
            </button>
          </div>
          <pre className="p-3 bg-dark-950 font-mono text-[11px] text-slate-300 overflow-x-auto whitespace-pre-wrap max-h-48 leading-relaxed selection:bg-accent-900/50">
            {item.sample_log}
          </pre>
        </div>
      )}
    </div>
  );
};
