import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import {
  Play,
  Pause,
  Sparkles,
  CheckSquare,
  Square,
  AlertTriangle,
  ArrowUp,
  Trash2,
  Info,
} from 'lucide-react';
import { LogEntry, LogFilterParams } from '../../types.ts';
import { SeverityBadge } from '../common/SeverityBadge.tsx';
import { LogSearchBar } from './LogSearchBar.tsx';
import { LogDetailModal } from './LogDetailModal.tsx';
import { fetchLogs, fetchLogFacets } from '../../api/logs.ts';
import { fetchAliases } from '../../api/aliases.ts';

function normalizeIsoString(ts: string): string {
  let parseable = ts.trim();
  if (!parseable.endsWith('Z') && !/[+-]\d{2}(:\d{2})?$/.test(parseable)) {
    parseable = parseable.replace(' ', 'T') + 'Z';
  }
  return parseable;
}

export function formatLocalTimestamp(ts: string, fallbackTs?: string): string {
  if (!ts && !fallbackTs) return '';
  try {
    let target = ts || fallbackTs || '';
    // If timestamp is clearly in the future compared to received_at (> 60s),
    // clamp to fallbackTs (received_at) to avoid 1-hour future offsets on legacy RFC 3164 rows
    if (ts && fallbackTs) {
      const dTs = new Date(normalizeIsoString(ts));
      const dFb = new Date(normalizeIsoString(fallbackTs));
      if (!isNaN(dTs.getTime()) && !isNaN(dFb.getTime()) && dTs.getTime() - dFb.getTime() > 60000) {
        target = fallbackTs;
      }
    }
    const parseable = normalizeIsoString(target);
    const d = new Date(parseable);
    if (isNaN(d.getTime())) {
      return target;
    }
    const hours = String(d.getHours()).padStart(2, '0');
    const minutes = String(d.getMinutes()).padStart(2, '0');
    const seconds = String(d.getSeconds()).padStart(2, '0');
    const millis = String(d.getMilliseconds()).padStart(3, '0');
    return `${hours}:${minutes}:${seconds}.${millis}`;
  } catch {
    return ts || fallbackTs || '';
  }
}

interface LiveLogStreamProps {
  onAnalyzeAi: (selectedLogs: LogEntry[]) => void;
  onAddAlias?: (ip: string) => void;
  knownAliases?: Record<string, string>;
}

const MAX_BUFFER_SIZE = 50000;

export const LiveLogStream: React.FC<LiveLogStreamProps> = ({
  onAnalyzeAi,
  onAddAlias,
  knownAliases = {},
}) => {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [autoScroll, setAutoScroll] = useState<boolean>(true);
  const [missedLogsCount, setMissedLogsCount] = useState<number>(0);
  const [selectedLogIds, setSelectedLogIds] = useState<Set<number>>(new Set());
  const [lastSelectedLogIndex, setLastSelectedLogIndex] = useState<number | null>(null);
  const [selectionHostError, setSelectionHostError] = useState<string | null>(null);
  const [activeLogDetail, setActiveLogDetail] = useState<LogEntry | null>(null);
  const [isLoadingHistory, setIsLoadingHistory] = useState<boolean>(false);
  const [filters, setFilters] = useState<LogFilterParams>({});
  const [activeAliasesMap, setActiveAliasesMap] = useState<Record<string, string>>({});

  useEffect(() => {
    fetchAliases()
      .then((list) => {
        const map: Record<string, string> = {};
        list.forEach((a) => {
          if (a.ip && a.alias) {
            map[a.ip] = a.alias;
          }
        });
        setActiveAliasesMap(map);
      })
      .catch((err) => {
        console.error('Failed to load host aliases in stream', err);
      });
  }, []);

  const mergedAliases = useMemo(() => {
    return { ...activeAliasesMap, ...knownAliases };
  }, [activeAliasesMap, knownAliases]);

  const parentRef = useRef<HTMLDivElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const isAutoScrollRef = useRef<boolean>(true);

  // Keep ref updated
  useEffect(() => {
    isAutoScrollRef.current = autoScroll;
  }, [autoScroll]);

  // Load initial logs on mount or on filter apply
  const loadInitialLogs = useCallback(async () => {
    try {
      setIsLoadingHistory(true);
      const res = await fetchLogs({
        ...filters,
        limit: 500,
        offset: 0,
      });
      // Keep newest logs at the top (res.logs is ordered DESC)
      setLogs(res.logs);
      setMissedLogsCount(0);
      setLastSelectedLogIndex(null);
      // Auto-scroll to top after initial load
      setTimeout(() => {
        if (parentRef.current) {
          parentRef.current.scrollTop = 0;
        }
      }, 50);
    } catch (err) {
      console.error('Failed to load initial logs', err);
    } finally {
      setIsLoadingHistory(false);
    }
  }, [filters]);

  useEffect(() => {
    loadInitialLogs();
  }, [loadInitialLogs]);

  const sourcesKey = useMemo(() => {
    const s = filters.sources || (filters.source ? (Array.isArray(filters.source) ? filters.source : [filters.source]) : []);
    return s.join(',');
  }, [filters.sources, filters.source]);

  const appsKey = useMemo(() => {
    const a = filters.apps || (filters.app_name ? (Array.isArray(filters.app_name) ? filters.app_name : [filters.app_name]) : []);
    return a.join(',');
  }, [filters.apps, filters.app_name]);

  // Connect to SSE stream
  useEffect(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    const params = new URLSearchParams();
    if (filters.severity_max !== undefined) params.set('severity_max', filters.severity_max.toString());
    if (sourcesKey) params.set('source', sourcesKey);
    if (appsKey) params.set('app_name', appsKey);

    const streamUrl = `/api/logs/stream${params.toString() ? `?${params.toString()}` : ''}`;
    const es = new EventSource(streamUrl);
    eventSourceRef.current = es;

    es.addEventListener('log', (event: MessageEvent) => {
      try {
        const entry: LogEntry = JSON.parse(event.data);
        setLogs((prev) => {
          const next = [entry, ...prev];
          if (next.length > MAX_BUFFER_SIZE) {
            return next.slice(0, MAX_BUFFER_SIZE);
          }
          return next;
        });

        if (!isAutoScrollRef.current) {
          setMissedLogsCount((prev) => prev + 1);
        } else if (parentRef.current) {
          requestAnimationFrame(() => {
            if (parentRef.current && isAutoScrollRef.current) {
              parentRef.current.scrollTop = 0;
            }
          });
        }
      } catch (e) {
        console.error('Error parsing SSE log event', e);
      }
    });

    es.onerror = (e) => {
      console.warn('SSE stream disconnected, reconnecting...', e);
    };

    return () => {
      es.close();
      eventSourceRef.current = null;
    };
  }, [filters.severity_max, sourcesKey, appsKey]);

  // Scroll detection to pause auto-scroll when scrolling down
  const handleScroll = () => {
    if (!parentRef.current) return;
    const { scrollTop } = parentRef.current;
    const isAtTop = scrollTop <= 10;

    if (isAtTop) {
      if (!autoScroll) {
        setAutoScroll(true);
        setMissedLogsCount(0);
      }
    } else {
      if (autoScroll) {
        setAutoScroll(false);
      }
    }
  };

  const resumeAutoScroll = () => {
    setAutoScroll(true);
    setMissedLogsCount(0);
    if (parentRef.current) {
      if (typeof parentRef.current.scrollTo === 'function') {
        parentRef.current.scrollTo({ top: 0, behavior: 'smooth' });
      } else {
        parentRef.current.scrollTop = 0;
      }
    }
  };

  // Virtualizer
  const rowVirtualizer = useVirtualizer({
    count: logs.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 28, // Compact row height in px
    overscan: 25,
  });

  // Accumulate permanent set of known hosts and apps so filter choices never vanish (Item #32 & Fix 2-Click Issue)
  const [accumulatedSources, setAccumulatedSources] = useState<string[]>([]);
  const [accumulatedApps, setAccumulatedApps] = useState<string[]>([]);

  useEffect(() => {
    setAccumulatedSources((prev) => {
      const aliasedIps = new Set(Object.keys(mergedAliases || {}));
      const set = new Set<string>();

      prev.forEach((s) => {
        if (mergedAliases && mergedAliases[s]) {
          set.add(mergedAliases[s]);
        } else if (!aliasedIps.has(s)) {
          set.add(s);
        }
      });

      if (mergedAliases) {
        Object.values(mergedAliases).forEach((alias) => {
          if (alias && alias.trim()) set.add(alias.trim());
        });
      }

      logs.forEach((log) => {
        const canonical = (log.source_ip && mergedAliases && mergedAliases[log.source_ip]) || log.source_alias;
        if (canonical && !aliasedIps.has(canonical)) {
          set.add(canonical);
        }
      });

      const next = Array.from(set).sort();
      if (next.length === prev.length && next.every((v, i) => v === prev[i])) {
        return prev;
      }
      return next;
    });

    setAccumulatedApps((prev) => {
      const set = new Set(prev);
      logs.forEach((log) => {
        if (log.app_name) set.add(log.app_name);
      });
      const next = Array.from(set).sort();
      if (next.length === prev.length && next.every((v, i) => v === prev[i])) {
        return prev;
      }
      return next;
    });
  }, [logs, mergedAliases]);

  // Merge accumulated sources & apps with current logs (ensuring all discovered items remain selectable)
  const allAvailableSources = useMemo(() => {
    const aliasedIps = new Set(Object.keys(mergedAliases || {}));
    const set = new Set<string>();

    accumulatedSources.forEach((src) => {
      if (mergedAliases && mergedAliases[src]) {
        set.add(mergedAliases[src]);
      } else if (!aliasedIps.has(src)) {
        set.add(src);
      }
    });

    logs.forEach((log) => {
      const canonical = (log.source_ip && mergedAliases && mergedAliases[log.source_ip]) || log.source_alias;
      if (canonical && !aliasedIps.has(canonical)) {
        set.add(canonical);
      }
    });

    if (mergedAliases) {
      Object.values(mergedAliases).forEach((alias) => {
        if (alias && alias.trim()) set.add(alias.trim());
      });
    }

    return Array.from(set).sort();
  }, [accumulatedSources, logs, mergedAliases]);

  const allAvailableApps = useMemo(() => {
    const set = new Set(accumulatedApps);
    logs.forEach((log) => {
      if (log.app_name) set.add(log.app_name);
    });
    return Array.from(set).sort();
  }, [accumulatedApps, logs]);

  // Accumulate bidirectional mappings: host -> apps AND app -> hosts
  const [hostToAppsMap, setHostToAppsMap] = useState<Record<string, string[]>>({});
  const [appToHostsMap, setAppToHostsMap] = useState<Record<string, string[]>>({});

  // Fetch full database facets on mount so all historical hosts and apps are available
  useEffect(() => {
    fetchLogFacets()
      .then((facets) => {
        if (facets.sources && facets.sources.length > 0) {
          setAccumulatedSources((prev) => Array.from(new Set([...prev, ...facets.sources])).sort());
        }
        if (facets.apps && facets.apps.length > 0) {
          setAccumulatedApps((prev) => Array.from(new Set([...prev, ...facets.apps])).sort());
        }
        if (facets.host_to_apps) {
          setHostToAppsMap((prev) => {
            const next = { ...facets.host_to_apps };
            Object.entries(prev).forEach(([h, apps]) => {
              if (!next[h]) next[h] = apps;
              else next[h] = Array.from(new Set([...next[h], ...apps])).sort();
            });
            return next;
          });
        }
        if (facets.app_to_hosts) {
          setAppToHostsMap((prev) => {
            const next = { ...facets.app_to_hosts };
            Object.entries(prev).forEach(([a, hosts]) => {
              if (!next[a]) next[a] = hosts;
              else next[a] = Array.from(new Set([...next[a], ...hosts])).sort();
            });
            return next;
          });
        }
      })
      .catch((err) => {
        console.error('Failed to load database facets', err);
      });
  }, []);

  useEffect(() => {
    setHostToAppsMap((prev) => {
      const nextMap: Record<string, Set<string>> = {};
      Object.entries(prev).forEach(([h, apps]) => {
        nextMap[h] = new Set(apps);
      });
      logs.forEach((l) => {
        if (l.app_name) {
          const canonicalHost = (l.source_ip && mergedAliases && mergedAliases[l.source_ip]) || l.source_alias || l.source_ip;
          if (canonicalHost) {
            if (!nextMap[canonicalHost]) nextMap[canonicalHost] = new Set();
            nextMap[canonicalHost].add(l.app_name);
          }
        }
      });
      if (mergedAliases) {
        Object.entries(mergedAliases).forEach(([ip, alias]) => {
          if (ip && alias) {
            if (nextMap[ip]) {
              if (!nextMap[alias]) nextMap[alias] = new Set();
              nextMap[ip].forEach((a) => nextMap[alias].add(a));
              delete nextMap[ip];
            }
          }
        });
      }
      const result: Record<string, string[]> = {};
      let changed = false;
      const allKeys = Object.keys(nextMap);
      if (allKeys.length !== Object.keys(prev).length) changed = true;
      for (const k of allKeys) {
        result[k] = Array.from(nextMap[k]).sort();
        if (!prev[k] || prev[k].length !== result[k].length) {
          changed = true;
        }
      }
      return changed ? result : prev;
    });

    setAppToHostsMap((prev) => {
      const nextMap: Record<string, Set<string>> = {};
      Object.entries(prev).forEach(([app, hosts]) => {
        nextMap[app] = new Set(hosts);
      });
      logs.forEach((l) => {
        if (l.app_name) {
          const canonicalHost = (l.source_ip && mergedAliases && mergedAliases[l.source_ip]) || l.source_alias || l.source_ip;
          if (canonicalHost) {
            if (!nextMap[l.app_name]) nextMap[l.app_name] = new Set();
            nextMap[l.app_name].add(canonicalHost);
          }
        }
      });
      if (mergedAliases) {
        Object.entries(mergedAliases).forEach(([ip, alias]) => {
          if (ip && alias) {
            Object.keys(nextMap).forEach((app) => {
              if (nextMap[app].has(ip)) {
                nextMap[app].delete(ip);
                nextMap[app].add(alias);
              }
            });
          }
        });
      }
      const result: Record<string, string[]> = {};
      let changed = false;
      const allKeys = Object.keys(nextMap);
      if (allKeys.length !== Object.keys(prev).length) changed = true;
      for (const k of allKeys) {
        result[k] = Array.from(nextMap[k]).sort();
        if (!prev[k] || prev[k].length !== result[k].length) {
          changed = true;
        }
      }
      return changed ? result : prev;
    });
  }, [logs, mergedAliases]);

  const activeSources: string[] = useMemo(() => {
    if (filters.sources && Array.isArray(filters.sources)) return filters.sources;
    if (filters.source) {
      if (Array.isArray(filters.source)) return filters.source;
      return filters.source.split(',').map((s) => s.trim()).filter(Boolean);
    }
    return [];
  }, [filters.sources, filters.source]);

  const activeApps: string[] = useMemo(() => {
    if (filters.apps && Array.isArray(filters.apps)) return filters.apps;
    if (filters.app_name) {
      if (Array.isArray(filters.app_name)) return filters.app_name;
      return filters.app_name.split(',').map((a) => a.trim()).filter(Boolean);
    }
    return [];
  }, [filters.apps, filters.app_name]);

  const toggleQuickSource = (src: string) => {
    setFilters((prev) => {
      const current = prev.sources || (prev.source ? (Array.isArray(prev.source) ? prev.source : [prev.source]) : []);
      const exists = current.includes(src);
      const next = exists ? current.filter((s) => s !== src) : [...current, src];
      return {
        ...prev,
        sources: next,
        source: next.length === 1 ? next[0] : (next.length > 1 ? next.join(',') : undefined),
      };
    });
  };

  const toggleQuickApp = (app: string) => {
    setFilters((prev) => {
      const current = prev.apps || (prev.app_name ? (Array.isArray(prev.app_name) ? prev.app_name : [prev.app_name]) : []);
      const exists = current.includes(app);
      const next = exists ? current.filter((a) => a !== app) : [...current, app];
      return {
        ...prev,
        apps: next,
        app_name: next.length === 1 ? next[0] : (next.length > 1 ? next.join(',') : undefined),
      };
    });
  };

  // Scope available apps to only those matching the selected host(s) if host(s) are chosen
  const availableAppsForSelectedHosts = useMemo(() => {
    if (activeSources.length === 0) {
      return allAvailableApps;
    }
    const set = new Set<string>();
    activeSources.forEach((src) => {
      const apps = hostToAppsMap[src];
      if (apps) {
        apps.forEach((a) => set.add(a));
      }
    });
    // Also check current buffer in case logs arrived matching active sources
    logs.forEach((l) => {
      if ((activeSources.includes(l.source_alias) || activeSources.includes(l.source_ip)) && l.app_name) {
        set.add(l.app_name);
      }
    });
    const result = Array.from(set).sort();
    return result.length > 0 ? result : allAvailableApps;
  }, [activeSources, allAvailableApps, hostToAppsMap, logs]);

  // Scope available sources to only those hosting the selected app(s) if app(s) are chosen
  const availableSourcesForSelectedApps = useMemo(() => {
    if (activeApps.length === 0) {
      return allAvailableSources;
    }
    const set = new Set<string>();
    activeApps.forEach((app) => {
      const hosts = appToHostsMap[app];
      if (hosts) {
        hosts.forEach((h) => set.add(h));
      }
    });
    // Also check current buffer in case logs arrived matching active apps
    logs.forEach((l) => {
      if (activeApps.includes(l.app_name) && l.source_alias) {
        set.add(l.source_alias);
      }
    });
    const result = Array.from(set).sort();
    return result.length > 0 ? result : allAvailableSources;
  }, [activeApps, allAvailableSources, appToHostsMap, logs]);

  // Multi-select with strict same-host constraint and Shift-click range support
  const toggleSelectLog = (log: LogEntry, index: number, e: React.MouseEvent) => {
    e.stopPropagation();
    setSelectionHostError(null);

    // Shift-Click Range Selection
    if (e.shiftKey && lastSelectedLogIndex !== null && lastSelectedLogIndex !== index) {
      const start = Math.min(lastSelectedLogIndex, index);
      const end = Math.max(lastSelectedLogIndex, index);
      const rangeLogs = logs.slice(start, end + 1);

      // Determine active host: from already selected logs, or fallback to anchor log
      const selectedEntries = logs.filter((l) => selectedLogIds.has(l.id));
      const activeHost = selectedEntries.length > 0
        ? selectedEntries[0].source_alias
        : (logs[lastSelectedLogIndex]?.source_alias || log.source_alias);

      const hasDisparateHosts = rangeLogs.some((l) => l.source_alias !== activeHost);
      const validLogs = rangeLogs.filter((l) => l.source_alias === activeHost);

      const newSet = new Set(selectedLogIds);
      validLogs.forEach((l) => newSet.add(l.id));
      setSelectedLogIds(newSet);
      setLastSelectedLogIndex(index);

      if (hasDisparateHosts) {
        setSelectionHostError(
          `Range selection contained logs from multiple hosts. Only logs matching host "${activeHost}" were selected.`
        );
        setTimeout(() => setSelectionHostError(null), 4000);
      }
      return;
    }

    // Normal single selection toggle
    const newSet = new Set(selectedLogIds);
    if (newSet.has(log.id)) {
      newSet.delete(log.id);
      setSelectedLogIds(newSet);
      setLastSelectedLogIndex(index);
      return;
    }

    // Check if other selected logs exist and verify same host
    if (newSet.size > 0) {
      const selectedEntries = logs.filter((l) => newSet.has(l.id));
      if (selectedEntries.length > 0) {
        const firstHost = selectedEntries[0].source_alias;
        if (firstHost !== log.source_alias) {
          setSelectionHostError(
            `Cannot select across different hosts. Selected logs must belong to "${firstHost}".`
          );
          setTimeout(() => setSelectionHostError(null), 4000);
          return;
        }
      }
    }

    newSet.add(log.id);
    setSelectedLogIds(newSet);
    setLastSelectedLogIndex(index);
  };

  const clearSelection = () => {
    setSelectedLogIds(new Set());
    setLastSelectedLogIndex(null);
    setSelectionHostError(null);
  };

  const handleLaunchAiAnalysis = () => {
    const selected = logs.filter((l) => selectedLogIds.has(l.id));
    if (selected.length > 0) {
      onAnalyzeAi(selected);
    }
  };

  const selectedLogs = useMemo(() => {
    return logs.filter((l) => selectedLogIds.has(l.id));
  }, [logs, selectedLogIds]);

  const clearLogsBuffer = () => {
    setLogs([]);
    setSelectedLogIds(new Set());
    setLastSelectedLogIndex(null);
    setMissedLogsCount(0);
  };

  return (
    <div className="flex flex-col h-[calc(100vh-45px)] bg-dark-950 select-text overflow-hidden">
      {/* Search & Filter Bar */}
      <LogSearchBar
        filters={filters}
        onFilterChange={setFilters}
        onSearch={loadInitialLogs}
        onReset={() => {
          setFilters({});
          loadInitialLogs();
        }}
        availableSources={availableSourcesForSelectedApps}
        availableApps={availableAppsForSelectedHosts}
      />

      {/* Stream Controls & Filter Pills Bar */}
      <div className="bg-dark-900 px-3 py-1.5 border-b border-dark-700 flex items-center justify-between text-xs select-none">
        {/* Left: Quick Filter Pills */}
        <div className="flex items-center gap-2 overflow-x-auto py-0.5">
          <span className="text-slate-400 font-medium text-[11px] shrink-0">Quick Filters:</span>
          {availableSourcesForSelectedApps.slice(0, 6).map((src) => {
            const isSelected = activeSources.includes(src);
            return (
              <button
                key={src}
                onClick={() => toggleQuickSource(src)}
                className={`px-2 py-0.5 rounded text-[11px] font-mono border transition cursor-pointer ${
                  isSelected
                    ? 'bg-accent-950 text-accent-300 border-accent-700 font-semibold'
                    : 'bg-dark-800 text-slate-300 border-dark-700 hover:border-slate-600'
                }`}
              >
                {src}
              </button>
            );
          })}
          {availableAppsForSelectedHosts.slice(0, 6).map((app) => {
            const isSelected = activeApps.includes(app);
            return (
              <button
                key={app}
                onClick={() => toggleQuickApp(app)}
                className={`px-2 py-0.5 rounded text-[11px] font-mono border transition cursor-pointer ${
                  isSelected
                    ? 'bg-indigo-950 text-indigo-300 border-indigo-700 font-semibold'
                    : 'bg-dark-800 text-slate-300 border-dark-700 hover:border-slate-600'
                }`}
              >
                {app}
              </button>
            );
          })}
        </div>

        {/* Right: Stream State Controls */}
        <div className="flex items-center gap-3 shrink-0">
          <span className="font-mono text-slate-400 text-[11px]">
            Screen Buffer: <span className="text-slate-200">{logs.length.toLocaleString()}</span> lines
          </span>

          <button
            onClick={() => (autoScroll ? setAutoScroll(false) : resumeAutoScroll())}
            className={`flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium border transition ${
              autoScroll
                ? 'bg-dark-800 text-emerald-400 border-emerald-900/60 hover:bg-dark-700'
                : 'bg-amber-950/60 text-amber-300 border-amber-800 hover:bg-amber-900/60'
            }`}
          >
            {autoScroll ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
            <span>{autoScroll ? 'Auto-Scroll ON' : 'Paused'}</span>
          </button>

          <button
            onClick={clearLogsBuffer}
            title="Clear screen buffer (clears browser view only; does not delete logs from disk)"
            aria-label="Clear screen buffer (clears browser view only; does not delete logs from disk)"
            className="p-1 text-slate-400 hover:text-red-400 hover:bg-dark-800 rounded transition"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Host Constraint Warning Toast */}
      {selectionHostError && (
        <div className="bg-amber-950/90 border-b border-amber-800 text-amber-200 px-4 py-1.5 text-xs flex items-center justify-between animate-in fade-in">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
            <span>{selectionHostError}</span>
          </div>
          <button onClick={() => setSelectionHostError(null)} className="text-amber-300 hover:text-white font-bold">
            ×
          </button>
        </div>
      )}

      {/* Virtualized Table View */}
      <div className="flex-1 relative overflow-hidden flex flex-col">
        {/* Table Header */}
        <div className="bg-dark-950 border-b border-dark-700 text-slate-400 text-[11px] font-mono font-semibold grid grid-cols-[36px_140px_65px_130px_130px_1fr_60px] px-3 py-1.5 select-none items-center">
          <div className="text-center">#</div>
          <div>TIMESTAMP</div>
          <div>SEV</div>
          <div>HOST / IP</div>
          <div>APP / CONTAINER</div>
          <div>MESSAGE</div>
          <div className="text-right pr-2">ACTIONS</div>
        </div>

        {/* Scrollable Virtualized Area */}
        <div
          ref={parentRef}
          onScroll={handleScroll}
          className="flex-1 overflow-y-auto overflow-x-hidden font-mono text-xs bg-dark-950 relative"
        >
          {isLoadingHistory && logs.length === 0 ? (
            <div className="flex items-center justify-center h-full text-slate-500 font-mono">
              Loading log history...
            </div>
          ) : logs.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-slate-500 font-mono space-y-2">
              <Info className="w-8 h-8 text-dark-600" />
              <span>No logs in stream. Waiting for incoming syslog or Docker events...</span>
            </div>
          ) : (
            <div
              style={{
                height: `${rowVirtualizer.getTotalSize()}px`,
                width: '100%',
                position: 'relative',
              }}
            >
              {rowVirtualizer.getVirtualItems().map((virtualRow) => {
                const log = logs[virtualRow.index];
                if (!log) return null;
                const isSelected = selectedLogIds.has(log.id);

                return (
                  <div
                    key={virtualRow.key}
                    data-index={virtualRow.index}
                    onClick={() => setActiveLogDetail(log)}
                    style={{
                      position: 'absolute',
                      top: 0,
                      left: 0,
                      width: '100%',
                      height: `${virtualRow.size}px`,
                      transform: `translateY(${virtualRow.start}px)`,
                    }}
                    className={`log-row grid grid-cols-[36px_140px_65px_130px_130px_1fr_60px] px-3 items-center border-b border-dark-900 cursor-pointer text-[11px] leading-tight ${
                      isSelected ? 'bg-accent-950/40 border-l-2 border-accent-500' : ''
                    }`}
                  >
                    {/* Checkbox */}
                    <div
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleSelectLog(log, virtualRow.index, e);
                      }}
                      className="h-full w-full flex items-center justify-center text-slate-500 hover:text-slate-200 cursor-pointer select-none"
                    >
                      {isSelected ? (
                        <CheckSquare className="w-3.5 h-3.5 text-accent-400" />
                      ) : (
                        <Square className="w-3.5 h-3.5 opacity-40 hover:opacity-100" />
                      )}
                    </div>

                    {/* Timestamp */}
                    <div
                      className="text-slate-400 truncate pr-2"
                      title={`UTC: ${log.timestamp}\nReceived: ${log.received_at}`}
                    >
                      {formatLocalTimestamp(log.timestamp, log.received_at)}
                    </div>

                    {/* Severity Badge */}
                    <div>
                      <SeverityBadge severity={log.severity} />
                    </div>

                    {/* Host Alias / IP */}
                    <div className="text-slate-300 truncate pr-2" title={`${log.source_alias} (${log.source_ip})`}>
                      {log.source_alias}
                    </div>

                    {/* App Name */}
                    <div className="text-slate-400 truncate pr-2 font-medium" title={log.app_name}>
                      {log.app_name}
                    </div>

                    {/* Raw Text Message without dangerouslySetInnerHTML */}
                    <div className="text-slate-200 truncate pr-3 select-text" title={log.message}>
                      {log.message}
                    </div>

                    {/* Quick Row Actions */}
                    <div className="flex items-center justify-end gap-1 pr-1" onClick={(e) => e.stopPropagation()}>
                      <button
                        onClick={() => {
                          onAnalyzeAi([log]);
                        }}
                        title="Explain with AI"
                        className="p-1 text-slate-400 hover:text-accent-400 hover:bg-dark-800 rounded transition"
                      >
                        <Sparkles className="w-3 h-3" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Floating Pause/Resume Banner */}
        {!autoScroll && missedLogsCount > 0 && (
          <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-20 shadow-xl animate-in slide-in-from-bottom-2">
            <button
              onClick={resumeAutoScroll}
              className="bg-accent-600 hover:bg-accent-500 text-white text-xs font-semibold px-4 py-2 rounded-full flex items-center gap-2 shadow-lg transition"
            >
              <ArrowUp className="w-3.5 h-3.5 animate-bounce" />
              <span>
                Auto-scroll paused ({missedLogsCount} new log{missedLogsCount === 1 ? '' : 's'} at top) — Click to jump to top
              </span>
            </button>
          </div>
        )}
      </div>

      {/* Floating Multi-Select Action Bar */}
      {selectedLogs.length > 0 && (
        <div className="bg-dark-900 border-t border-dark-700 px-4 py-2.5 flex items-center justify-between select-none z-20 shadow-2xl animate-in slide-in-from-bottom">
          <div className="flex items-center gap-3">
            <span className="text-xs font-semibold text-slate-200">
              {selectedLogs.length} log{selectedLogs.length === 1 ? '' : 's'} selected
            </span>
            <span className="text-xs text-slate-400 font-mono">
              Host: <span className="text-accent-400">{selectedLogs[0].source_alias}</span>
            </span>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={clearSelection}
              className="px-3 py-1 text-xs text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded transition"
            >
              Clear Selection
            </button>

            <button
              onClick={handleLaunchAiAnalysis}
              className="bg-accent-600 hover:bg-accent-500 text-white text-xs font-medium px-4 py-1.5 rounded-lg flex items-center gap-1.5 transition shadow-md"
            >
              <Sparkles className="w-3.5 h-3.5" />
              <span>Analyze ({selectedLogs.length}) Selected Logs with AI</span>
            </button>
          </div>
        </div>
      )}

      {/* Log Detail Slide-Over Inspector */}
      <LogDetailModal
        log={activeLogDetail}
        isOpen={Boolean(activeLogDetail)}
        onClose={() => setActiveLogDetail(null)}
        onExplainWithAi={(log, ctxLogs) => {
          setActiveLogDetail(null);
          const logsToAnalyze = ctxLogs && ctxLogs.length > 0 ? ctxLogs : [log];
          setLogs((prevLogs) => {
            const existingIds = new Set(prevLogs.map((l) => l.id));
            const missingLogs = logsToAnalyze.filter((l) => !existingIds.has(l.id));
            if (missingLogs.length === 0) return prevLogs;
            return [...missingLogs, ...prevLogs].sort((a, b) => {
              const cmp = b.timestamp.localeCompare(a.timestamp);
              return cmp !== 0 ? cmp : b.id - a.id;
            });
          });
          setSelectedLogIds(new Set(logsToAnalyze.map((l) => l.id)));
          onAnalyzeAi(logsToAnalyze);
        }}
        onAnalyzeWithContext={(targetAndCtxLogs) => {
          setActiveLogDetail(null);
          setLogs((prevLogs) => {
            const existingIds = new Set(prevLogs.map((l) => l.id));
            const missingLogs = targetAndCtxLogs.filter((l) => !existingIds.has(l.id));
            if (missingLogs.length === 0) return prevLogs;
            return [...missingLogs, ...prevLogs].sort((a, b) => {
              const cmp = b.timestamp.localeCompare(a.timestamp);
              return cmp !== 0 ? cmp : b.id - a.id;
            });
          });
          setSelectedLogIds(new Set(targetAndCtxLogs.map((l) => l.id)));
          onAnalyzeAi(targetAndCtxLogs);
        }}
        onAddAlias={onAddAlias}
        isHostAliased={
          activeLogDetail
            ? Boolean(
                mergedAliases[activeLogDetail.source_ip] ||
                (activeLogDetail.source_alias && activeLogDetail.source_alias !== activeLogDetail.source_ip)
              )
            : true
        }
      />
    </div>
  );
};
