import React, { useState } from 'react';
import { Trash2, Clock, Check, RefreshCw, AlertCircle } from 'lucide-react';
import { triggerManualPrune } from '../../api/system.ts';
import { formatBytes } from './StorageCard.tsx';
import { PruneResponse } from '../../types.ts';

interface RetentionSliderProps {
  retentionDays: number;
  onSaveRetention: (days: number) => Promise<void>;
  onPruneCompleted?: () => void;
}

export const RetentionSlider: React.FC<RetentionSliderProps> = ({
  retentionDays,
  onSaveRetention,
  onPruneCompleted,
}) => {
  const [days, setDays] = useState<number>(retentionDays);
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [saveSuccess, setSaveSuccess] = useState<boolean>(false);

  const [isPruning, setIsPruning] = useState<boolean>(false);
  const [pruneResult, setPruneResult] = useState<PruneResponse | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleSave = async () => {
    try {
      setIsSaving(true);
      setErrorMsg(null);
      await onSaveRetention(days);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 2500);
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to update retention period.');
    } finally {
      setIsSaving(false);
    }
  };

  const handlePruneNow = async () => {
    if (!window.confirm('Trigger immediate database prune and vacuum now?')) return;
    try {
      setIsPruning(true);
      setErrorMsg(null);
      const res = await triggerManualPrune();
      setPruneResult(res);
      if (onPruneCompleted) onPruneCompleted();
    } catch (err: any) {
      setErrorMsg(err.message || 'Manual prune failed.');
    } finally {
      setIsPruning(false);
    }
  };

  return (
    <div className="bg-dark-900 border border-dark-700 rounded-xl p-5 shadow-md space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
          <Clock className="w-4 h-4 text-accent-500" />
          <span>Log Retention & Vacuum Policy</span>
        </h3>
        <span className="font-mono text-xs text-accent-400 font-bold">
          {days} Day{days === 1 ? '' : 's'}
        </span>
      </div>

      {errorMsg && (
        <div className="p-3 bg-red-950/60 border border-red-800 rounded-lg flex items-start gap-2 text-xs text-red-300">
          <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
          <span>{errorMsg}</span>
        </div>
      )}

      {/* Slider Control */}
      <div>
        <input
          type="range"
          min="1"
          max="30"
          value={days}
          onChange={(e) => setDays(parseInt(e.target.value, 10))}
          className="w-full accent-accent-500 bg-dark-950 h-2 rounded-lg cursor-pointer"
        />
        <div className="flex justify-between text-[10px] font-mono text-slate-500 mt-1">
          <span>1 Day</span>
          <span>7 Days</span>
          <span>14 Days</span>
          <span>21 Days</span>
          <span>30 Days</span>
        </div>
      </div>

      {/* Action Buttons */}
      <div className="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-dark-800">
        <button
          onClick={handleSave}
          disabled={isSaving || days === retentionDays}
          className="bg-dark-800 hover:bg-dark-700 disabled:opacity-40 text-slate-200 border border-dark-600 font-medium px-4 py-1.5 rounded-lg text-xs flex items-center gap-1.5 transition"
        >
          {saveSuccess ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Clock className="w-3.5 h-3.5" />}
          <span>{saveSuccess ? 'Saved!' : 'Save Retention Policy'}</span>
        </button>

        <button
          onClick={handlePruneNow}
          disabled={isPruning}
          className="bg-red-950/80 hover:bg-red-900 text-red-300 border border-red-800 font-medium px-4 py-1.5 rounded-lg text-xs flex items-center gap-1.5 transition shadow-xs"
        >
          {isPruning ? (
            <>
              <RefreshCw className="w-3.5 h-3.5 animate-spin" />
              <span>Pruning & Truncating WAL...</span>
            </>
          ) : (
            <>
              <Trash2 className="w-3.5 h-3.5" />
              <span>Prune & Vacuum Now</span>
            </>
          )}
        </button>
      </div>

      {/* Prune Result Banner */}
      {pruneResult && (
        <div className="p-3 bg-dark-950 border border-emerald-900/60 rounded-lg text-xs font-mono text-slate-300 animate-in fade-in">
          <span className="text-emerald-400 font-semibold block mb-1">Prune Completed Successfully:</span>
          <div className="grid grid-cols-2 gap-2 text-[11px]">
            <div>Deleted Records: <span className="text-slate-100">{pruneResult.deleted_logs}</span></div>
            <div>Deleted Metrics: <span className="text-slate-100">{pruneResult.deleted_metrics}</span></div>
            <div>Current DB Footprint: <span className="text-slate-100">{formatBytes(pruneResult.metrics.db_size_bytes)}</span></div>
          </div>
        </div>
      )}
    </div>
  );
};
