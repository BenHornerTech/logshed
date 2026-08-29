import React, { useEffect, useState } from 'react';
import { Activity, Server, Settings, LogOut, Radio, RefreshCw } from 'lucide-react';
import { useAuth } from '../../context/AuthContext.tsx';
import { fetchHealth } from '../../api/system.ts';
import { HealthResponse } from '../../types.ts';

interface NavbarProps {
  activeTab: 'stream' | 'aliases' | 'settings';
  onTabChange: (tab: 'stream' | 'aliases' | 'settings') => void;
  isStreaming?: boolean;
}

export const Navbar: React.FC<NavbarProps> = ({ activeTab, onTabChange, isStreaming = false }) => {
  const { logout } = useAuth();
  const [health, setHealth] = useState<HealthResponse | null>(null);

  const loadHealth = async () => {
    try {
      const res = await fetchHealth();
      setHealth(res);
    } catch {
      // Health fetch error handled gracefully
    }
  };

  useEffect(() => {
    loadHealth();
    const interval = setInterval(loadHealth, 10000);
    return () => clearInterval(interval);
  }, []);

  return (
    <header className="bg-dark-950 border-b border-dark-700 px-4 py-2 flex items-center justify-between select-none sticky top-0 z-30">
      {/* Brand & Status Indicator */}
      <div className="flex items-center space-x-4">
        <div className="flex items-center space-x-2">
          <Activity className="w-5 h-5 text-accent-500" />
          <span className="font-semibold tracking-wide text-slate-100 text-sm">
            HOMELAB <span className="text-accent-500 font-mono">LOG HUB</span>
          </span>
        </div>

        {/* Live SSE Stream Pulse */}
        <div className="flex items-center space-x-1.5 px-2 py-0.5 rounded bg-dark-900 border border-dark-700 text-xs font-mono">
          <span className={`w-2 h-2 rounded-full ${isStreaming ? 'bg-emerald-500 animate-pulse' : 'bg-slate-500'}`} />
          <span className="text-slate-300 text-[11px]">
            {isStreaming ? 'LIVE' : 'IDLE'}
          </span>
        </div>

        {/* Ingestion & Queue Health */}
        {health && (
          <div className="hidden md:flex items-center space-x-3 text-xs font-mono text-slate-400 border-l border-dark-700 pl-4">
            <span title="Queue Depth">
              Queue: <span className="text-slate-200">{health.queue_depth}</span>
            </span>
            <span title="Dropped Logs Total">
              Dropped:{' '}
              <span className={health.dropped_logs > 0 ? 'text-amber-400 font-bold' : 'text-slate-200'}>
                {health.dropped_logs}
              </span>
            </span>
          </div>
        )}
      </div>

      {/* Center Navigation Tabs */}
      <nav className="flex items-center space-x-1 bg-dark-900 p-1 rounded-lg border border-dark-700">
        <button
          onClick={() => onTabChange('stream')}
          className={`flex items-center space-x-1.5 px-3 py-1 text-xs font-medium rounded-md transition-all ${
            activeTab === 'stream'
              ? 'bg-dark-700 text-slate-100 shadow-sm'
              : 'text-slate-400 hover:text-slate-200 hover:bg-dark-800'
          }`}
        >
          <Radio className="w-3.5 h-3.5" />
          <span>Console View</span>
        </button>

        <button
          onClick={() => onTabChange('aliases')}
          className={`flex items-center space-x-1.5 px-3 py-1 text-xs font-medium rounded-md transition-all ${
            activeTab === 'aliases'
              ? 'bg-dark-700 text-slate-100 shadow-sm'
              : 'text-slate-400 hover:text-slate-200 hover:bg-dark-800'
          }`}
        >
          <Server className="w-3.5 h-3.5" />
          <span>Host Aliases</span>
        </button>

        <button
          onClick={() => onTabChange('settings')}
          className={`flex items-center space-x-1.5 px-3 py-1 text-xs font-medium rounded-md transition-all ${
            activeTab === 'settings'
              ? 'bg-dark-700 text-slate-100 shadow-sm'
              : 'text-slate-400 hover:text-slate-200 hover:bg-dark-800'
          }`}
        >
          <Settings className="w-3.5 h-3.5" />
          <span>Settings & Storage</span>
        </button>
      </nav>

      {/* Right Controls */}
      <div className="flex items-center space-x-2">
        <button
          onClick={loadHealth}
          title="Refresh Health"
          className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded border border-transparent hover:border-dark-700 transition"
        >
          <RefreshCw className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={logout}
          title="Sign Out"
          className="flex items-center space-x-1 px-2.5 py-1 text-xs font-medium text-slate-400 hover:text-red-300 hover:bg-red-950/30 rounded border border-transparent hover:border-red-900 transition"
        >
          <LogOut className="w-3.5 h-3.5" />
          <span className="hidden sm:inline">Logout</span>
        </button>
      </div>
    </header>
  );
};
