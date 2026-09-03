import React, { useState } from 'react';
import { Search, RotateCcw, Filter, Clock, X } from 'lucide-react';
import { LogFilterParams } from '../../types.ts';

interface LogSearchBarProps {
  filters: LogFilterParams;
  onFilterChange: (filters: LogFilterParams) => void;
  onSearch: () => void;
  onReset: () => void;
  availableSources?: string[];
  availableApps?: string[];
}

export const LogSearchBar: React.FC<LogSearchBarProps> = ({
  filters,
  onFilterChange,
  onSearch,
  onReset,
  availableSources = [],
  availableApps = [],
}) => {
  const [timePreset, setTimePreset] = useState<string>('all');
  const [showCustomTime, setShowCustomTime] = useState<boolean>(false);

  const handleTimePresetChange = (preset: string) => {
    setTimePreset(preset);
    if (preset === 'custom') {
      setShowCustomTime(true);
      return;
    }

    setShowCustomTime(false);
    if (preset === 'all') {
      onFilterChange({ ...filters, from: undefined, to: undefined });
      return;
    }

    const now = new Date();
    let fromDate = new Date();
    if (preset === '15m') fromDate = new Date(now.getTime() - 15 * 60 * 1000);
    else if (preset === '1h') fromDate = new Date(now.getTime() - 60 * 60 * 1000);
    else if (preset === '6h') fromDate = new Date(now.getTime() - 6 * 60 * 60 * 1000);
    else if (preset === '24h') fromDate = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    else if (preset === '7d') fromDate = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);

    onFilterChange({
      ...filters,
      from: fromDate.toISOString(),
      to: undefined,
    });
  };

  return (
    <div className="bg-dark-950 border-b border-dark-700 p-2.5 flex flex-col gap-2 select-none text-xs">
      {/* Top Row: Search Input & Action Buttons */}
      <div className="flex items-center gap-2">
        {/* FTS Search Input */}
        <div className="relative flex-1">
          <input
            type="text"
            value={filters.query || ''}
            onChange={(e) => onFilterChange({ ...filters, query: e.target.value })}
            onKeyDown={(e) => e.key === 'Enter' && onSearch()}
            placeholder="Full-text search (FTS5 syntax: error AND NOT timeout, 'kernel panic', status:*)..."
            className="w-full bg-dark-900 border border-dark-700 rounded px-3 py-1.5 pl-8 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
          />
          <Search className="w-3.5 h-3.5 text-slate-500 absolute left-2.5 top-2" />
          {filters.query && (
            <button
              onClick={() => onFilterChange({ ...filters, query: '' })}
              className="absolute right-2 top-1.5 text-slate-500 hover:text-slate-300"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>

        {/* Severity Filter Dropdown */}
        <div className="flex items-center gap-1.5">
          <Filter className="w-3.5 h-3.5 text-slate-400" />
          <select
            value={filters.severity_max !== undefined ? filters.severity_max : ''}
            onChange={(e) => {
              const val = e.target.value === '' ? undefined : parseInt(e.target.value, 10);
              onFilterChange({ ...filters, severity_max: val });
            }}
            className="bg-dark-900 border border-dark-700 rounded px-2 py-1.5 text-xs text-slate-200 focus:outline-hidden focus:border-accent-500 font-mono"
          >
            <option value="">All Severities</option>
            <option value="0">≤ Emerg (0)</option>
            <option value="1">≤ Alert (1)</option>
            <option value="2">≤ Crit (2)</option>
            <option value="3">≤ Error (3)</option>
            <option value="4">≤ Warn (4)</option>
            <option value="5">≤ Notice (5)</option>
            <option value="6">≤ Info (6)</option>
            <option value="7">≤ Debug (7)</option>
          </select>
        </div>

        {/* Time Preset Selector */}
        <div className="flex items-center gap-1.5">
          <Clock className="w-3.5 h-3.5 text-slate-400" />
          <select
            value={timePreset}
            onChange={(e) => handleTimePresetChange(e.target.value)}
            className="bg-dark-900 border border-dark-700 rounded px-2 py-1.5 text-xs text-slate-200 focus:outline-hidden focus:border-accent-500 font-mono"
          >
            <option value="all">All Time</option>
            <option value="15m">Last 15m</option>
            <option value="1h">Last 1h</option>
            <option value="6h">Last 6h</option>
            <option value="24h">Last 24h</option>
            <option value="7d">Last 7d</option>
            <option value="custom">Custom...</option>
          </select>
        </div>

        {/* Action Buttons */}
        <button
          onClick={onSearch}
          className="bg-accent-600 hover:bg-accent-500 text-white font-medium px-3 py-1.5 rounded flex items-center gap-1 transition shadow-xs"
        >
          <Search className="w-3.5 h-3.5" />
          <span>Filter</span>
        </button>

        <button
          onClick={() => {
            setTimePreset('all');
            setShowCustomTime(false);
            onReset();
          }}
          title="Reset Filters"
          className="bg-dark-900 hover:bg-dark-800 border border-dark-700 text-slate-300 px-2.5 py-1.5 rounded flex items-center gap-1 transition"
        >
          <RotateCcw className="w-3.5 h-3.5" />
          <span>Reset</span>
        </button>
      </div>

      {/* Second Row: Specific Dimension Filters & Custom Time */}
      <div className="flex flex-wrap items-center gap-2 pt-1 border-t border-dark-800">
        {/* Source Filter */}
        <div className="flex items-center gap-1">
          <span className="text-slate-400 text-[11px]">Host / IP:</span>
          {availableSources.length > 0 ? (
            <select
              value={filters.source || ''}
              onChange={(e) => onFilterChange({ ...filters, source: e.target.value || undefined })}
              className="bg-dark-900 border border-dark-700 rounded px-2 py-1 text-[11px] text-slate-200 font-mono focus:outline-hidden focus:border-accent-500"
            >
              <option value="">All Hosts</option>
              {availableSources.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              value={filters.source || ''}
              onChange={(e) => onFilterChange({ ...filters, source: e.target.value || undefined })}
              placeholder="Filter host/IP..."
              className="bg-dark-900 border border-dark-700 rounded px-2 py-1 text-[11px] text-slate-200 font-mono w-32 focus:outline-hidden focus:border-accent-500"
            />
          )}
        </div>

        {/* App Filter */}
        <div className="flex items-center gap-1">
          <span className="text-slate-400 text-[11px]">App / Container:</span>
          {availableApps.length > 0 ? (
            <select
              value={filters.app_name || ''}
              onChange={(e) => onFilterChange({ ...filters, app_name: e.target.value || undefined })}
              className="bg-dark-900 border border-dark-700 rounded px-2 py-1 text-[11px] text-slate-200 font-mono focus:outline-hidden focus:border-accent-500"
            >
              <option value="">All Apps</option>
              {availableApps.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              value={filters.app_name || ''}
              onChange={(e) => onFilterChange({ ...filters, app_name: e.target.value || undefined })}
              placeholder="Filter container/app..."
              className="bg-dark-900 border border-dark-700 rounded px-2 py-1 text-[11px] text-slate-200 font-mono w-36 focus:outline-hidden focus:border-accent-500"
            />
          )}
        </div>

        {/* Custom Datetime Pickers */}
        {showCustomTime && (
          <div className="flex items-center gap-2 bg-dark-900 px-2 py-1 rounded border border-dark-700">
            <div className="flex items-center gap-1">
              <span className="text-slate-400 text-[11px]">From:</span>
              <input
                type="datetime-local"
                value={filters.from ? filters.from.slice(0, 16) : ''}
                onChange={(e) =>
                  onFilterChange({
                    ...filters,
                    from: e.target.value ? new Date(e.target.value).toISOString() : undefined,
                  })
                }
                className="bg-dark-950 border border-dark-700 rounded px-1.5 py-0.5 text-[11px] text-slate-200 font-mono"
              />
            </div>
            <div className="flex items-center gap-1">
              <span className="text-slate-400 text-[11px]">To:</span>
              <input
                type="datetime-local"
                value={filters.to ? filters.to.slice(0, 16) : ''}
                onChange={(e) =>
                  onFilterChange({
                    ...filters,
                    to: e.target.value ? new Date(e.target.value).toISOString() : undefined,
                  })
                }
                className="bg-dark-950 border border-dark-700 rounded px-1.5 py-0.5 text-[11px] text-slate-200 font-mono"
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
