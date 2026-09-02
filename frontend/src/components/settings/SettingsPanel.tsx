import React, { useEffect, useState } from 'react';
import {
  Settings,
  Brain,
  Bell,
  Lock,
  History,
  Check,
  Send,
  Save,
  AlertCircle,
  RefreshCw,
  Copy,
  Trash2,
} from 'lucide-react';
import { StorageMetricsResponse, AiAuditEntry } from '../../types.ts';
import { fetchSettings, updateSettings, SettingsResponseData } from '../../api/settings.ts';
import { fetchStorageMetrics } from '../../api/system.ts';
import { testNotifications, sendPushoverNotification } from '../../api/notifications.ts';
import { fetchAiAudit, deleteAiAuditItem, clearAiAuditLog } from '../../api/ai.ts';
import { changePassword } from '../../api/auth.ts';
import { copyToClipboard } from '../../utils/clipboard.ts';
import { Modal } from '../common/Modal.tsx';
import { MarkdownRenderer } from '../common/MarkdownRenderer.tsx';
import { StorageCard } from './StorageCard.tsx';
import { RetentionSlider } from './RetentionSlider.tsx';
import { StorageTrendChart } from './StorageTrendChart.tsx';

export const SettingsPanel: React.FC = () => {
  const [settings, setSettings] = useState<SettingsResponseData | null>(null);
  const [storageMetrics, setStorageMetrics] = useState<StorageMetricsResponse | null>(null);
  const [auditLogs, setAuditLogs] = useState<AiAuditEntry[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [saveSuccessMsg, setSaveSuccessMsg] = useState<string | null>(null);

  // Form states
  const [aiProvider, setAiProvider] = useState<'gemini' | 'openai' | 'openai_compatible'>('gemini');
  const [aiModel, setAiModel] = useState<string>('gemini-2.5-flash');
  const [aiApiKey, setAiApiKey] = useState<string>('');
  const [aiBaseUrl, setAiBaseUrl] = useState<string>('');

  const [pushoverUserKey, setPushoverUserKey] = useState<string>('');
  const [pushoverAppToken, setPushoverAppToken] = useState<string>('');
  const [isTestingPushover, setIsTestingPushover] = useState<boolean>(false);
  const [pushoverTestMsg, setPushoverTestMsg] = useState<string | null>(null);

  // Password reset state
  const [currentPwd, setCurrentPwd] = useState<string>('');
  const [newPwd, setNewPwd] = useState<string>('');
  const [confirmPwd, setConfirmPwd] = useState<string>('');
  const [isChangingPwd, setIsChangingPwd] = useState<boolean>(false);
  const [pwdMsg, setPwdMsg] = useState<{ text: string; isError: boolean } | null>(null);

  // AI Audit Inspection modal state
  const [selectedAuditItem, setSelectedAuditItem] = useState<AiAuditEntry | null>(null);
  const [copiedAuditPrompt, setCopiedAuditPrompt] = useState<boolean>(false);
  const [isSendingAuditPushover, setIsSendingAuditPushover] = useState<boolean>(false);
  const [auditPushoverMsg, setAuditPushoverMsg] = useState<string | null>(null);
  const [showPromptDetails, setShowPromptDetails] = useState<boolean>(false);
  const [isDeletingAuditId, setIsDeletingAuditId] = useState<number | null>(null);
  const [showClearAllAuditModal, setShowClearAllAuditModal] = useState<boolean>(false);
  const [isClearingAllAudit, setIsClearingAllAudit] = useState<boolean>(false);

  const loadAllData = async () => {
    try {
      setIsLoading(true);
      setErrorMsg(null);

      const [settRes, storRes, auditRes] = await Promise.all([
        fetchSettings(),
        fetchStorageMetrics(),
        fetchAiAudit(20, 0).catch(() => ({ items: [], total: 0 })),
      ]);

      setSettings(settRes);
      setStorageMetrics(storRes);
      setAuditLogs(auditRes.items);

      // Populate form
      setAiProvider(settRes.ai_provider);
      setAiModel(settRes.ai_model || 'gemini-2.5-flash');
      setAiApiKey(settRes.ai_api_key || '');
      setAiBaseUrl(settRes.ai_base_url || '');
      setPushoverUserKey(settRes.pushover_user_key || '');
      setPushoverAppToken(settRes.pushover_app_token || '');
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to load system settings.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadAllData();
  }, []);

  const handleSaveSettings = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      setErrorMsg(null);
      setSaveSuccessMsg(null);

      await updateSettings({
        ai_provider: aiProvider,
        ai_model: aiModel,
        ai_api_key: aiApiKey,
        ai_base_url: aiBaseUrl || null,
        pushover_user_key: pushoverUserKey,
        pushover_app_token: pushoverAppToken,
      });

      setSaveSuccessMsg('Settings saved successfully and secrets encrypted.');
      setTimeout(() => setSaveSuccessMsg(null), 3500);
      await loadAllData();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to update settings.');
    }
  };

  const handleSaveRetention = async (retentionDays: number) => {
    await updateSettings({ retention_days: retentionDays });
    if (settings) {
      setSettings({ ...settings, retention_days: retentionDays });
    }
  };

  const handleTestPushover = async () => {
    try {
      setIsTestingPushover(true);
      setPushoverTestMsg(null);
      const res = await testNotifications();
      setPushoverTestMsg(res.detail || 'Pushover test message dispatched successfully.');
    } catch (err: any) {
      setPushoverTestMsg(`Error: ${err.message}`);
    } finally {
      setIsTestingPushover(false);
    }
  };

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (newPwd.length < 8) {
      setPwdMsg({ text: 'New password must be at least 8 characters long.', isError: true });
      return;
    }
    if (newPwd !== confirmPwd) {
      setPwdMsg({ text: 'New passwords do not match.', isError: true });
      return;
    }

    try {
      setIsChangingPwd(true);
      setPwdMsg(null);
      await changePassword(currentPwd, newPwd);
      setPwdMsg({ text: 'Admin password changed successfully.', isError: false });
      setCurrentPwd('');
      setNewPwd('');
      setConfirmPwd('');
      setTimeout(() => setPwdMsg(null), 3500);
    } catch (err: any) {
      setPwdMsg({ text: err.message || 'Failed to update password.', isError: true });
    } finally {
      setIsChangingPwd(false);
    }
  };

  const handleCopyAuditPrompt = async () => {
    if (selectedAuditItem?.prompt_sent) {
      const ok = await copyToClipboard(selectedAuditItem.prompt_sent);
      if (ok) {
        setCopiedAuditPrompt(true);
        setTimeout(() => setCopiedAuditPrompt(false), 2000);
      }
    }
  };

  const handleSendAuditPushover = async () => {
    if (!selectedAuditItem) return;
    try {
      setIsSendingAuditPushover(true);
      setAuditPushoverMsg(null);
      const title = `[Log Hub Audit] ${selectedAuditItem.source_alias}: ${selectedAuditItem.app_name}`;
      await sendPushoverNotification({
        title,
        message: selectedAuditItem.response_text,
        priority: 0,
      });
      setAuditPushoverMsg('Historical analysis dispatched to Pushover successfully!');
      setTimeout(() => setAuditPushoverMsg(null), 4000);
    } catch (err: any) {
      setAuditPushoverMsg(`Failed to send Pushover notification: ${err.message}`);
    } finally {
      setIsSendingAuditPushover(false);
    }
  };

  const handleDeleteAuditItem = async (id: number, e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    try {
      setIsDeletingAuditId(id);
      await deleteAiAuditItem(id);
      setAuditLogs((prev) => prev.filter((item) => item.id !== id));
      if (selectedAuditItem && selectedAuditItem.id === id) {
        setSelectedAuditItem(null);
      }
    } catch (err: any) {
      alert(`Failed to delete AI analysis: ${err.message || err}`);
    } finally {
      setIsDeletingAuditId(null);
    }
  };

  const handleClearAllAudit = async () => {
    try {
      setIsClearingAllAudit(true);
      await clearAiAuditLog();
      setAuditLogs([]);
      setSelectedAuditItem(null);
      setShowClearAllAuditModal(false);
    } catch (err: any) {
      alert(`Failed to clear AI audit log: ${err.message || err}`);
    } finally {
      setIsClearingAllAudit(false);
    }
  };

  if (isLoading && !settings) {
    return (
      <div className="p-8 text-center text-slate-500 font-mono text-xs flex items-center justify-center gap-2">
        <RefreshCw className="w-4 h-4 animate-spin text-accent-500" />
        <span>Loading system settings and storage metrics...</span>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-8">
      {/* Header */}
      <div>
        <h2 className="text-base font-bold text-slate-100 flex items-center gap-2">
          <Settings className="w-5 h-5 text-accent-500" />
          <span>System Settings & Storage Dashboard</span>
        </h2>
        <p className="text-xs text-slate-400 mt-0.5">
          Configure on-demand AI LLM providers, Pushover alert credentials, retention policy, and monitor disk storage.
        </p>
      </div>

      {errorMsg && (
        <div className="p-3 bg-red-950/60 border border-red-800 rounded-lg flex items-start gap-2 text-xs text-red-300">
          <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
          <span>{errorMsg}</span>
        </div>
      )}

      {saveSuccessMsg && (
        <div className="p-3 bg-emerald-950/60 border border-emerald-800 rounded-lg flex items-start gap-2 text-xs text-emerald-300">
          <Check className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
          <span>{saveSuccessMsg}</span>
        </div>
      )}

      {/* Storage & Retention Section */}
      <section className="space-y-4">
        <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
          Storage & Retention
        </h3>
        <StorageCard metrics={storageMetrics} />
        {settings && (
          <RetentionSlider
            retentionDays={settings.retention_days}
            onSaveRetention={handleSaveRetention}
            onPruneCompleted={loadAllData}
          />
        )}
        <StorageTrendChart history={storageMetrics?.history || []} />
      </section>

      {/* Settings Form: AI & Notifications */}
      <form onSubmit={handleSaveSettings} className="space-y-6">
        {/* AI Provider Section */}
        <section className="bg-dark-900 border border-dark-700 rounded-xl p-5 shadow-md space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
              <Brain className="w-4 h-4 text-accent-500" />
              <span>On-Demand AI Provider Configuration</span>
            </h3>
            <span className="text-[11px] font-mono text-emerald-400">Encrypted at rest</span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                AI Provider
              </label>
              <select
                value={aiProvider}
                onChange={(e) => setAiProvider(e.target.value as any)}
                className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-200 focus:outline-hidden focus:border-accent-500 font-mono"
              >
                <option value="gemini">Google Gemini</option>
                <option value="openai">OpenAI</option>
                <option value="openai_compatible">OpenAI-Compatible (Ollama / vLLM / LocalAI)</option>
              </select>
            </div>

            <div>
              <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                Default Model Name
              </label>
              <input
                type="text"
                value={aiModel}
                onChange={(e) => setAiModel(e.target.value)}
                placeholder={aiProvider === 'gemini' ? 'gemini-2.5-flash' : aiProvider === 'openai' ? 'gpt-4o' : 'llama3.2'}
                className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
              />
            </div>

            <div>
              <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                API Key (Masked)
              </label>
              <input
                type="password"
                value={aiApiKey}
                onChange={(e) => setAiApiKey(e.target.value)}
                placeholder="Enter API key or leave ******** to preserve"
                className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
              />
            </div>

            <div>
              <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                Custom Base URL (Optional)
              </label>
              <input
                type="text"
                value={aiBaseUrl}
                onChange={(e) => setAiBaseUrl(e.target.value)}
                placeholder="http://host.docker.internal:11434/v1"
                className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
              />
            </div>
          </div>
        </section>

        {/* Pushover Notifications Section */}
        <section className="bg-dark-900 border border-dark-700 rounded-xl p-5 shadow-md space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
              <Bell className="w-4 h-4 text-accent-500" />
              <span>Pushover Notifications</span>
            </h3>
            <button
              type="button"
              onClick={handleTestPushover}
              disabled={isTestingPushover}
              className="px-3 py-1 bg-dark-800 hover:bg-dark-700 text-slate-200 border border-dark-600 rounded text-xs flex items-center gap-1.5 transition"
            >
              <Send className="w-3 h-3 text-accent-400" />
              <span>{isTestingPushover ? 'Testing...' : 'Send Test Notification'}</span>
            </button>
          </div>

          {pushoverTestMsg && (
            <div className="p-2.5 bg-dark-950 border border-dark-700 rounded text-xs font-mono text-slate-300">
              {pushoverTestMsg}
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                Pushover User Key
              </label>
              <input
                type="password"
                value={pushoverUserKey}
                onChange={(e) => setPushoverUserKey(e.target.value)}
                placeholder="Enter Pushover User Key..."
                className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
              />
            </div>

            <div>
              <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                Pushover Application Token
              </label>
              <input
                type="password"
                value={pushoverAppToken}
                onChange={(e) => setPushoverAppToken(e.target.value)}
                placeholder="Enter Pushover App Token..."
                className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
              />
            </div>
          </div>
        </section>

        {/* Submit Button for General Settings */}
        <div className="flex justify-end">
          <button
            type="submit"
            className="bg-accent-600 hover:bg-accent-500 text-white font-medium px-5 py-2 rounded-lg text-xs flex items-center gap-2 transition shadow-md"
          >
            <Save className="w-4 h-4" />
            <span>Save Application Settings</span>
          </button>
        </div>
      </form>

      {/* Admin Password Reset Section */}
      <section className="bg-dark-900 border border-dark-700 rounded-xl p-5 shadow-md space-y-4">
        <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
          <Lock className="w-4 h-4 text-accent-500" />
          <span>Change Admin Password</span>
        </h3>

        {pwdMsg && (
          <div
            className={`p-3 rounded-lg border text-xs flex items-start gap-2 ${
              pwdMsg.isError
                ? 'bg-red-950/60 border-red-800 text-red-300'
                : 'bg-emerald-950/60 border-emerald-800 text-emerald-300'
            }`}
          >
            {pwdMsg.isError ? (
              <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
            ) : (
              <Check className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
            )}
            <span>{pwdMsg.text}</span>
          </div>
        )}

        <form onSubmit={handleChangePassword} className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-end">
          <div>
            <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
              Current Password
            </label>
            <input
              type="password"
              value={currentPwd}
              onChange={(e) => setCurrentPwd(e.target.value)}
              required
              className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
            />
          </div>

          <div>
            <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
              New Password (min 8 chars)
            </label>
            <input
              type="password"
              value={newPwd}
              onChange={(e) => setNewPwd(e.target.value)}
              minLength={8}
              required
              className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
            />
          </div>

          <div>
            <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
              Confirm New Password
            </label>
            <input
              type="password"
              value={confirmPwd}
              onChange={(e) => setConfirmPwd(e.target.value)}
              minLength={8}
              required
              className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
            />
          </div>

          <div className="sm:col-span-3 flex justify-end pt-1">
            <button
              type="submit"
              disabled={isChangingPwd || !currentPwd || newPwd.length < 8 || newPwd !== confirmPwd}
              className="bg-dark-800 hover:bg-dark-700 disabled:opacity-50 text-slate-200 border border-dark-600 font-medium px-4 py-2 rounded-lg text-xs flex items-center gap-1.5 transition"
            >
              <Lock className="w-3.5 h-3.5" />
              <span>{isChangingPwd ? 'Updating...' : 'Update Password'}</span>
            </button>
          </div>
        </form>
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

        {auditLogs.length === 0 ? (
          <div className="p-6 text-center text-slate-500 font-mono text-xs">
            No AI analyses executed yet. Select logs in the stream and click "Explain with AI".
          </div>
        ) : (
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
                  setAuditPushoverMsg(null);
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
                <div className="text-slate-200 font-sans text-xs truncate pr-2 group-hover:text-white">
                  {item.response_text.slice(0, 100)}...
                </div>
                <div className="flex items-center justify-end gap-1.5" onClick={(e) => e.stopPropagation()}>
                  <button
                    type="button"
                    onClick={() => {
                      setSelectedAuditItem(item);
                      setShowPromptDetails(false);
                      setAuditPushoverMsg(null);
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
          onClose={() => setSelectedAuditItem(null)}
          title="Historical AI Root-Cause Analysis"
          maxWidth="max-w-3xl"
        >
          <div className="space-y-4 text-xs font-sans">
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

            {/* Collapsible Sanitized Prompt / Logs */}
            <div className="border border-dark-700 rounded-lg overflow-hidden bg-dark-950">
              <div className="flex items-center justify-between px-3 py-2 bg-dark-900 border-b border-dark-700">
                <button
                  type="button"
                  onClick={() => setShowPromptDetails(!showPromptDetails)}
                  className="text-[11px] font-semibold text-slate-300 uppercase tracking-wider hover:text-slate-100 transition cursor-pointer"
                >
                  {showPromptDetails ? '▼ Hide Submitted Logs & Prompt' : '▶ View Submitted Logs & Prompt'}
                </button>
                {showPromptDetails && (
                  <button
                    type="button"
                    onClick={handleCopyAuditPrompt}
                    className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-slate-200 transition cursor-pointer"
                  >
                    {copiedAuditPrompt ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                    <span>{copiedAuditPrompt ? 'Copied' : 'Copy Prompt'}</span>
                  </button>
                )}
              </div>
              {showPromptDetails && (
                <pre className="p-3 overflow-x-auto text-xs font-mono text-slate-300 max-h-48 overflow-y-auto leading-relaxed select-text">
                  <code>{selectedAuditItem.prompt_sent}</code>
                </pre>
              )}
            </div>

            {auditPushoverMsg && (
              <div className="p-2.5 bg-dark-800 border border-dark-700 rounded text-xs text-slate-300">
                {auditPushoverMsg}
              </div>
            )}

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
                  onClick={handleSendAuditPushover}
                  disabled={isSendingAuditPushover}
                  className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white font-medium px-4 py-2 rounded-lg text-xs flex items-center gap-2 transition shadow-md cursor-pointer"
                >
                  <Send className="w-3.5 h-3.5" />
                  <span>{isSendingAuditPushover ? 'Sending...' : 'Send to Pushover'}</span>
                </button>
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
          onClose={() => setShowClearAllAuditModal(false)}
          title="Clear AI Audit Log"
          maxWidth="max-w-md"
        >
          <div className="space-y-4 text-xs font-sans">
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
