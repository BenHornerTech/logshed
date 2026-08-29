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
} from 'lucide-react';
import { StorageMetricsResponse, AiAuditEntry } from '../../types.ts';
import { fetchSettings, updateSettings, SettingsResponseData } from '../../api/settings.ts';
import { fetchStorageMetrics } from '../../api/system.ts';
import { testNotifications } from '../../api/notifications.ts';
import { fetchAiAudit } from '../../api/ai.ts';
import { changePassword } from '../../api/auth.ts';
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
                placeholder="gemini-2.5-flash / gpt-4o / llama3.2"
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
        </div>

        {auditLogs.length === 0 ? (
          <div className="p-6 text-center text-slate-500 font-mono text-xs">
            No AI analyses executed yet. Select logs in the stream and click "Explain with AI".
          </div>
        ) : (
          <div className="divide-y divide-dark-800 font-mono text-xs">
            <div className="grid grid-cols-[140px_160px_140px_80px_1fr] px-4 py-2 text-slate-400 font-semibold text-[11px] bg-dark-950/60 select-none">
              <div>TIMESTAMP</div>
              <div>HOST • APP</div>
              <div>MODEL</div>
              <div>TOKENS</div>
              <div>SUMMARY</div>
            </div>

            {auditLogs.map((item) => (
              <div
                key={item.id}
                className="grid grid-cols-[140px_160px_140px_80px_1fr] px-4 py-2.5 items-start hover:bg-dark-800/50 transition text-[11px]"
              >
                <div className="text-slate-400">{item.timestamp.slice(0, 19).replace('T', ' ')}</div>
                <div className="text-slate-300 truncate pr-2">
                  <span className="font-semibold text-accent-400">{item.source_alias}</span>
                  <span className="text-slate-500"> • {item.app_name}</span>
                </div>
                <div className="text-slate-400 truncate">{item.model}</div>
                <div className="text-slate-300">{item.tokens_used}</div>
                <div className="text-slate-200 font-sans text-xs truncate pr-2">
                  {item.response_text.slice(0, 100)}...
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
};
