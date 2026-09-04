import React, { useState } from 'react';
import { useAuth } from './context/AuthContext.tsx';
import { Navbar } from './components/common/Navbar.tsx';
import { LoginForm } from './components/auth/LoginForm.tsx';
import { SetupModal } from './components/auth/SetupModal.tsx';
import { LiveLogStream } from './components/logs/LiveLogStream.tsx';
import { HostAliasManager } from './components/aliases/HostAliasManager.tsx';
import { SettingsPanel } from './components/settings/SettingsPanel.tsx';
import { AiAnalysisModal } from './components/ai/AiAnalysisModal.tsx';
import { LogEntry } from './types.ts';
import { LogShedLogo } from './components/common/LogShedLogo.tsx';

export const App: React.FC = () => {
  const { isAuthenticated, setupRequired, isLoading } = useAuth();
  const [activeTab, setActiveTab] = useState<'stream' | 'aliases' | 'settings'>('stream');
  const [aiSelectedLogs, setAiSelectedLogs] = useState<LogEntry[]>([]);
  const [addAliasIp, setAddAliasIp] = useState<string | null>(null);

  if (isLoading) {
    return (
      <div className="min-h-screen bg-dark-950 flex flex-col items-center justify-center text-slate-400 font-mono text-xs space-y-3">
        <LogShedLogo className="w-8 h-8 text-accent-500 animate-pulse" />
        <span>Loading LogShed...</span>
      </div>
    );
  }

  if (setupRequired) {
    return <SetupModal />;
  }

  if (!isAuthenticated) {
    return <LoginForm />;
  }

  const handleOpenAiModal = (logs: LogEntry[]) => {
    setAiSelectedLogs(logs);
  };

  const handleAddAliasFromLog = (ip: string) => {
    setAddAliasIp(ip);
    setActiveTab('aliases');
  };

  return (
    <div className="min-h-screen bg-dark-950 text-slate-200 flex flex-col">
      {/* Top Fixed Navbar */}
      <Navbar
        activeTab={activeTab}
        onTabChange={setActiveTab}
        isStreaming={activeTab === 'stream'}
      />

      {/* Main Content Area */}
      <main className="flex-1">
        {activeTab === 'stream' && (
          <LiveLogStream
            onDiagnoseAi={handleOpenAiModal}
            onAddAlias={handleAddAliasFromLog}
          />
        )}

        {activeTab === 'aliases' && (
          <HostAliasManager
            initialAddIp={addAliasIp}
            onAliasSaved={() => setAddAliasIp(null)}
          />
        )}

        {activeTab === 'settings' && <SettingsPanel />}
      </main>

      {/* Global AI Root-Cause Analysis Modal */}
      <AiAnalysisModal
        isOpen={aiSelectedLogs.length > 0}
        onClose={() => setAiSelectedLogs([])}
        selectedLogs={aiSelectedLogs}
      />
    </div>
  );
};
