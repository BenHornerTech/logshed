import React, { useEffect, useLayoutEffect, useRef, useState, useCallback, useMemo } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import {
  Play,
  Pause,
  Sparkles,
  CheckSquare,
  Square,
  ArrowUp,
  Trash2,
  Info,
  Clock,
  Loader2,
  AlertCircle,
} from 'lucide-react';
import { LogEntry, LogFilterParams } from '../../types.ts';
import { SeverityBadge } from '../common/SeverityBadge.tsx';
import { LogSearchBar } from './LogSearchBar.tsx';
import { LogDetailModal } from './LogDetailModal.tsx';
import { fetchLogs, fetchLogFacets } from '../../api/logs.ts';
import { fetchAliases } from '../../api/aliases.ts';

function cleanIsoString(ts: string): string {
  let parseable = ts.trim();
  if (!parseable.endsWith('Z') && !/[+-]\d{2}(:\d{2})?$/.test(parseable)) {
    parseable = parseable.replace(' ', 'T') + 'Z';
  }
  return parseable;
}

const MONTH_NAMES = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

export function formatLocalTimestamp(ts: string, fallbackTs?: string): string {
  if (!ts && !fallbackTs) return '';
  try {
    let target = ts || fallbackTs || '';
    // If timestamp is clearly in the future compared to received_at (> 60s),
    // clamp to fallbackTs (received_at) to avoid 1-hour future offsets on legacy RFC 3164 rows
    if (ts && fallbackTs) {
      const dTs = new Date(cleanIsoString(ts));
      const dFb = new Date(cleanIsoString(fallbackTs));
      if (!isNaN(dTs.getTime()) && !isNaN(dFb.getTime()) && dTs.getTime() - dFb.getTime() > 60000) {
        target = fallbackTs;
      }
    }
    const parseable = cleanIsoString(target);
    const d = new Date(parseable);
    if (isNaN(d.getTime())) {
      return target;
    }

    const now = new Date();
    const isToday =
      d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() &&
      d.getDate() === now.getDate();

    const hours = String(d.getHours()).padStart(2, '0');
    const minutes = String(d.getMinutes()).padStart(2, '0');
    const seconds = String(d.getSeconds()).padStart(2, '0');

    if (isToday) {
      const millis = String(d.getMilliseconds()).padStart(3, '0');
      return `${hours}:${minutes}:${seconds}.${millis}`;
    }

    const day = String(d.getDate()).padStart(2, '0');
    const month = MONTH_NAMES[d.getMonth()];
    return `${day} ${month} ${hours}:${minutes}:${seconds}`;
  } catch {
    return ts || fallbackTs || '';
  }
}

export function matchesSearchQuery(log: LogEntry, query?: string): boolean {
  if (!query || !query.trim()) return true;

  const q = query.trim();
  const fields = [
    log.message || '',
    log.app_name || '',
    log.source_alias || '',
    log.source_ip || '',
  ];
  const combined = fields.join(' ').toLowerCase();

  // 1. Direct case-insensitive substring match
  if (combined.includes(q.toLowerCase())) {
    return true;
  }

  // 2. Try regex match (e.g. if query contains regex patterns)
  try {
    const regex = new RegExp(q, 'i');
    if (fields.some((f) => regex.test(f))) {
      return true;
    }
  } catch {
    // If not a valid regex, continue with token matching
  }

  // 3. Multi-term matching (e.g. "nginx error" -> all terms must match)
  const terms = q
    .toLowerCase()
    .split(/\s+/)
    .filter((t) => t.length > 0 && !['and', 'or', 'not'].includes(t));

  if (terms.length > 0) {
    const allTermsMatch = terms.every((term) => {
      // Check for column-specific search (e.g. app_name:nginx or app:nginx)
      if (term.includes(':')) {
        const [col, val] = term.split(':', 2);
        const cleanVal = val.replace(/^["'*]+|["'*]+$/g, '');
        if (!cleanVal) return true;
        if (col === 'app_name' || col === 'app') {
          return (log.app_name || '').toLowerCase().includes(cleanVal);
        }
        if (col === 'source' || col === 'source_alias' || col === 'host') {
          return (
            (log.source_alias || '').toLowerCase().includes(cleanVal) ||
            (log.source_ip || '').toLowerCase().includes(cleanVal)
          );
        }
        if (col === 'message' || col === 'msg') {
          return (log.message || '').toLowerCase().includes(cleanVal);
        }
      }

      const cleanTerm = term.replace(/^["'*]+|["'*]+$/g, '');
      if (!cleanTerm) return true;
      return combined.includes(cleanTerm);
    });

    if (allTermsMatch) {
      return true;
    }
  }

  return false;
}

interface LiveLogStreamProps {
  onDiagnoseAi: (selectedLogs: LogEntry[]) => void;
  onAddAlias?: (ip: string) => void;
  knownAliases?: Record<string, string>;
}

const MAX_BUFFER_SIZE = 50000;
const BATCH_FLUSH_INTERVAL_MS = 100;

export const LiveLogStream: React.FC<LiveLogStreamProps> = ({
  onDiagnoseAi,
  onAddAlias,
  knownAliases = {},
}) => {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [autoScroll, setAutoScroll] = useState<boolean>(true);
  const [missedLogsCount, setMissedLogsCount] = useState<number>(0);
  const [selectedLogIds, setSelectedLogIds] = useState<Set<number>>(new Set());
  const [lastSelectedLogIndex, setLastSelectedLogIndex] = useState<number | null>(null);
  const [activeLogDetail, setActiveLogDetail] = useState<LogEntry | null>(null);
  const [isLoadingHistory, setIsLoadingHistory] = useState<boolean>(false);
  const [isLoadingMore, setIsLoadingMore] = useState<boolean>(false);
  const [hasMoreLogs, setHasMoreLogs] = useState<boolean>(true);
  const historicalOffsetRef = useRef<number>(0);
  const isLoadingMoreRef = useRef<boolean>(false);
  const [filters, setFilters] = useState<LogFilterParams>({});
  const [activeAliasesMap, setActiveAliasesMap] = useState<Record<string, string>>({});

  // Decoupled facet accumulation states
  const [accumulatedSources, setAccumulatedSources] = useState<string[]>([]);
  const [accumulatedApps, setAccumulatedApps] = useState<string[]>([]);
  const [hostToAppsMap, setHostToAppsMap] = useState<Record<string, string[]>>({});
  const [appToHostsMap, setAppToHostsMap] = useState<Record<string, string[]>>({});

  // Incoming SSE batch buffer & flush timer
  const incomingBufferRef = useRef<LogEntry[]>([]);
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const filtersRef = useRef<LogFilterParams>(filters);

  useEffect(() => {
    filtersRef.current = filters;
  }, [filters]);

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

  const mergedAliasesRef = useRef(mergedAliases);
  useEffect(() => {
    mergedAliasesRef.current = mergedAliases;
  }, [mergedAliases]);

  const parentRef = useRef<HTMLDivElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const isAutoScrollRef = useRef<boolean>(true);
  const pendingScrollAdjustmentRef = useRef<number>(0);

  // Keep ref updated
  useEffect(() => {
    isAutoScrollRef.current = autoScroll;
  }, [autoScroll]);

  // Maintain scroll anchoring when new logs arrive while auto-scroll is paused
  useLayoutEffect(() => {
    if (pendingScrollAdjustmentRef.current > 0) {
      const adjustment = pendingScrollAdjustmentRef.current;
      pendingScrollAdjustmentRef.current = 0;
      if (parentRef.current && !isAutoScrollRef.current) {
        parentRef.current.scrollTop += adjustment;
      }
    }
  }, [logs]);

  // Incrementally accumulate facets from incoming batches without rescanning 50k logs
  const updateFacetsWithNewLogs = useCallback((newLogs: LogEntry[]) => {
    if (!newLogs || newLogs.length === 0) return;

    // 1. Incrementally add any new sources
    setAccumulatedSources((prev) => {
      let added = false;
      const currentSet = new Set(prev);
      const aliasedIps = new Set(Object.keys(mergedAliasesRef.current || {}));
      for (const log of newLogs) {
        const canonical =
          (log.source_ip && mergedAliasesRef.current[log.source_ip]) ||
          (log.source_alias && mergedAliasesRef.current[log.source_alias]) ||
          log.source_alias ||
          log.source_ip;
        if (canonical && !aliasedIps.has(canonical) && !currentSet.has(canonical)) {
          currentSet.add(canonical);
          added = true;
        }
      }
      return added ? Array.from(currentSet).sort() : prev;
    });

    // 2. Incrementally add any new apps
    setAccumulatedApps((prev) => {
      let added = false;
      const currentSet = new Set(prev);
      for (const log of newLogs) {
        if (log.app_name && !currentSet.has(log.app_name)) {
          currentSet.add(log.app_name);
          added = true;
        }
      }
      return added ? Array.from(currentSet).sort() : prev;
    });

    // 3. Incrementally update hostToAppsMap
    setHostToAppsMap((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const log of newLogs) {
        if (log.app_name) {
          const canonical =
            (log.source_ip && mergedAliasesRef.current[log.source_ip]) ||
            (log.source_alias && mergedAliasesRef.current[log.source_alias]) ||
            log.source_alias ||
            log.source_ip;
          if (canonical) {
            const currentApps = next[canonical] || [];
            if (!currentApps.includes(log.app_name)) {
              next[canonical] = [...currentApps, log.app_name].sort();
              changed = true;
            }
          }
        }
      }
      return changed ? next : prev;
    });

    // 4. Incrementally update appToHostsMap
    setAppToHostsMap((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const log of newLogs) {
        if (log.app_name) {
          const canonical =
            (log.source_ip && mergedAliasesRef.current[log.source_ip]) ||
            (log.source_alias && mergedAliasesRef.current[log.source_alias]) ||
            log.source_alias ||
            log.source_ip;
          if (canonical) {
            const currentHosts = next[log.app_name] || [];
            if (!currentHosts.includes(canonical)) {
              next[log.app_name] = [...currentHosts, canonical].sort();
              changed = true;
            }
          }
        }
      }
      return changed ? next : prev;
    });
  }, []);

  // Flush incoming buffered SSE logs to React state in a single batch
  const flushIncomingLogs = useCallback(() => {
    if (incomingBufferRef.current.length === 0) return;

    const toFlush = incomingBufferRef.current;
    incomingBufferRef.current = [];

    // Incrementally update facets with new incoming logs
    updateFacetsWithNewLogs(toFlush);

    setLogs((prev) => {
      // toFlush is in arrival order (oldest first, newest last)
      // Reverse toFlush so newest log is at the top (index 0)
      const next = [...toFlush.slice().reverse(), ...prev];
      if (next.length > MAX_BUFFER_SIZE) {
        return next.slice(0, MAX_BUFFER_SIZE);
      }
      return next;
    });

    setLastSelectedLogIndex((prev) => (prev !== null ? prev + toFlush.length : null));

    if (!isAutoScrollRef.current) {
      setMissedLogsCount((prev) => prev + toFlush.length);
      // Anchor scroll position by compensating for prepended items' height (28px each)
      pendingScrollAdjustmentRef.current += toFlush.length * 28;
    } else if (parentRef.current) {
      requestAnimationFrame(() => {
        if (parentRef.current && isAutoScrollRef.current) {
          parentRef.current.scrollTop = 0;
        }
      });
    }
  }, [updateFacetsWithNewLogs]);

  // In-flight query cancellation / stale response guard
  const fetchRequestIdRef = useRef<number>(0);

  // Load initial logs on mount or on filter apply
  const loadInitialLogs = useCallback(async (overrideFilters?: LogFilterParams) => {
    const reqId = ++fetchRequestIdRef.current;
    try {
      setIsLoadingHistory(true);
      historicalOffsetRef.current = 0;
      setHasMoreLogs(true);
      if (flushTimerRef.current !== null) {
        clearTimeout(flushTimerRef.current);
        flushTimerRef.current = null;
      }
      incomingBufferRef.current = [];
      pendingScrollAdjustmentRef.current = 0;

      const activeFilters = overrideFilters !== undefined ? overrideFilters : filters;
      const res = await fetchLogs({
        ...activeFilters,
        limit: 500,
        offset: 0,
      });

      // Discard stale responses from superseded requests
      if (reqId !== fetchRequestIdRef.current) {
        return;
      }

      // Keep newest logs at the top (res.logs is ordered DESC)
      setLogs(res.logs);
      updateFacetsWithNewLogs(res.logs);
      historicalOffsetRef.current = res.logs.length;
      if (res.logs.length < 500 || (res.total !== undefined && res.logs.length >= res.total)) {
        setHasMoreLogs(false);
      }
      setMissedLogsCount(0);
      setLastSelectedLogIndex(null);
      // Auto-scroll to top after initial load
      setTimeout(() => {
        if (parentRef.current && isAutoScrollRef.current) {
          parentRef.current.scrollTop = 0;
        }
      }, 50);
    } catch (err) {
      if (reqId === fetchRequestIdRef.current) {
        console.error('Failed to load initial logs', err);
      }
    } finally {
      if (reqId === fetchRequestIdRef.current) {
        setIsLoadingHistory(false);
      }
    }
  }, [filters, updateFacetsWithNewLogs]);

  const loadMoreLogs = useCallback(async () => {
    if (isLoadingMoreRef.current || !hasMoreLogs || isLoadingHistory) return;
    isLoadingMoreRef.current = true;
    setIsLoadingMore(true);
    const reqId = fetchRequestIdRef.current;
    try {
      const currentOffset = historicalOffsetRef.current;
      const res = await fetchLogs({
        ...filters,
        limit: 500,
        offset: currentOffset,
      });

      // Discard stale responses if filter changed while loading more
      if (reqId !== fetchRequestIdRef.current) {
        return;
      }

      historicalOffsetRef.current = currentOffset + res.logs.length;
      if (res.logs.length < 500 || (res.total !== undefined && historicalOffsetRef.current >= res.total)) {
        setHasMoreLogs(false);
      }
      if (res.logs.length > 0) {
        updateFacetsWithNewLogs(res.logs);
        setLogs((prev) => {
          const existingIds = new Set(prev.map((l) => l.id));
          const uniqueIncoming = res.logs.filter((l) => !existingIds.has(l.id));
          if (uniqueIncoming.length === 0 && res.logs.length > 0) {
            if (res.logs.length < 500) {
              setHasMoreLogs(false);
            }
            return prev;
          }
          return [...prev, ...uniqueIncoming];
        });
      }
    } catch (err) {
      if (reqId === fetchRequestIdRef.current) {
        console.error('Failed to load more historical logs', err);
      }
    } finally {
      isLoadingMoreRef.current = false;
      setIsLoadingMore(false);
    }
  }, [filters, hasMoreLogs, isLoadingHistory, updateFacetsWithNewLogs]);

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
      eventSourceRef.current = null;
    }

    // If viewing a bounded historical range (to is specified), pause/skip live incoming SSE events
    if (filters.to) {
      return;
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

        // Client-Side Query Filtering on Ingest (Issue #2)
        const currentQuery = filtersRef.current?.query;
        if (currentQuery && !matchesSearchQuery(entry, currentQuery)) {
          return;
        }

        // Buffer incoming SSE logs (Issue #1)
        incomingBufferRef.current.push(entry);

        if (incomingBufferRef.current.length >= 500) {
          if (flushTimerRef.current !== null) {
            clearTimeout(flushTimerRef.current);
            flushTimerRef.current = null;
          }
          flushIncomingLogs();
        } else if (flushTimerRef.current === null) {
          flushTimerRef.current = setTimeout(() => {
            flushTimerRef.current = null;
            flushIncomingLogs();
          }, BATCH_FLUSH_INTERVAL_MS);
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
      if (flushTimerRef.current !== null) {
        clearTimeout(flushTimerRef.current);
        flushTimerRef.current = null;
      }
      incomingBufferRef.current = [];
    };
  }, [filters.severity_max, filters.to, sourcesKey, appsKey, flushIncomingLogs]);

  // Scroll detection to pause auto-scroll when scrolling down, and load more when near bottom
  const handleScroll = () => {
    if (!parentRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = parentRef.current;
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

    // If operator scrolled near bottom (< 300px from bottom), load older logs
    if (scrollHeight - scrollTop - clientHeight < 300) {
      if (hasMoreLogs && !isLoadingMoreRef.current && !isLoadingHistory) {
        loadMoreLogs();
      }
    }
  };

  const resumeAutoScroll = () => {
    setAutoScroll(true);
    setMissedLogsCount(0);
    pendingScrollAdjustmentRef.current = 0;
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

  const virtualItems = rowVirtualizer.getVirtualItems();
  const lastVirtualItem = virtualItems.length > 0 ? virtualItems[virtualItems.length - 1] : null;

  useEffect(() => {
    if (!lastVirtualItem) return;
    if (
      lastVirtualItem.index >= logs.length - 15 &&
      hasMoreLogs &&
      !isLoadingMoreRef.current &&
      !isLoadingHistory
    ) {
      loadMoreLogs();
    }
  }, [lastVirtualItem, logs.length, hasMoreLogs, isLoadingHistory, loadMoreLogs]);

  // Merge accumulated sources & apps with aliases (ensuring all discovered items remain selectable)
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

    if (mergedAliases) {
      Object.values(mergedAliases).forEach((alias) => {
        if (alias && alias.trim()) set.add(alias.trim());
      });
    }

    return Array.from(set).sort();
  }, [accumulatedSources, mergedAliases]);

  const allAvailableApps = useMemo(() => {
    return Array.from(new Set(accumulatedApps)).sort();
  }, [accumulatedApps]);

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

  // Remap host/app relations whenever aliases are added or modified
  useEffect(() => {
    if (!mergedAliases || Object.keys(mergedAliases).length === 0) return;

    setAccumulatedSources((prev) => {
      const aliasedIps = new Set(Object.keys(mergedAliases));
      const set = new Set<string>();
      let changed = false;

      prev.forEach((s) => {
        if (mergedAliases[s]) {
          set.add(mergedAliases[s]);
          changed = true;
        } else if (!aliasedIps.has(s)) {
          set.add(s);
        } else {
          changed = true;
        }
      });

      Object.values(mergedAliases).forEach((alias) => {
        if (alias && alias.trim() && !set.has(alias.trim())) {
          set.add(alias.trim());
          changed = true;
        }
      });

      return changed ? Array.from(set).sort() : prev;
    });

    setHostToAppsMap((prev) => {
      let changed = false;
      const next = { ...prev };
      Object.entries(mergedAliases).forEach(([ip, alias]) => {
        if (ip && alias && next[ip]) {
          const existingForAlias = next[alias] || [];
          next[alias] = Array.from(new Set([...existingForAlias, ...next[ip]])).sort();
          delete next[ip];
          changed = true;
        }
      });
      return changed ? next : prev;
    });

    setAppToHostsMap((prev) => {
      let changed = false;
      const next = { ...prev };
      Object.entries(mergedAliases).forEach(([ip, alias]) => {
        if (ip && alias) {
          Object.keys(next).forEach((app) => {
            if (next[app]?.includes(ip)) {
              next[app] = Array.from(new Set(next[app].map((h) => (h === ip ? alias : h)))).sort();
              changed = true;
            }
          });
        }
      });
      return changed ? next : prev;
    });
  }, [mergedAliases]);

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
    const result = Array.from(set).sort();
    return result.length > 0 ? result : allAvailableApps;
  }, [activeSources, allAvailableApps, hostToAppsMap]);

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
    const result = Array.from(set).sort();
    return result.length > 0 ? result : allAvailableSources;
  }, [activeApps, allAvailableSources, appToHostsMap]);

  // Multi-select across single or multiple hosts with Shift-click range support
  const toggleSelectLog = (log: LogEntry, index: number, e: React.MouseEvent) => {
    e.stopPropagation();

    // Shift-Click Range Selection
    if (e.shiftKey && lastSelectedLogIndex !== null && lastSelectedLogIndex !== index) {
      const start = Math.min(lastSelectedLogIndex, index);
      const end = Math.max(lastSelectedLogIndex, index);
      const rangeLogs = logs.slice(start, end + 1);

      const newSet = new Set(selectedLogIds);
      rangeLogs.forEach((l) => newSet.add(l.id));
      setSelectedLogIds(newSet);
      setLastSelectedLogIndex(index);
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

    newSet.add(log.id);
    setSelectedLogIds(newSet);
    setLastSelectedLogIndex(index);
  };

  const selectAllLogs = () => {
    // Select all logs loaded in the client-side buffer, capped at the 200-log AI analysis ceiling
    const targetLogs = logs.slice(0, 200);
    const newSet = new Set(targetLogs.map((l) => l.id));
    setSelectedLogIds(newSet);
  };

  const deselectAllLogs = () => {
    setSelectedLogIds(new Set());
    setLastSelectedLogIndex(null);
  };

  const selectedLogs = useMemo(() => {
    return logs.filter((l) => selectedLogIds.has(l.id));
  }, [logs, selectedLogIds]);

  const handleLaunchAiAnalysis = () => {
    if (selectedLogs.length > 0) {
      onDiagnoseAi(selectedLogs);
    }
  };

  const clearLogsBuffer = () => {
    fetchRequestIdRef.current += 1;
    if (flushTimerRef.current !== null) {
      clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
    incomingBufferRef.current = [];
    pendingScrollAdjustmentRef.current = 0;
    setLogs([]);
    setSelectedLogIds(new Set());
    setLastSelectedLogIndex(null);
    setMissedLogsCount(0);
    setHasMoreLogs(false);
  };

  const handleResetFilters = useCallback(() => {
    setFilters({});
    setAutoScroll(true);
    isAutoScrollRef.current = true;
    setMissedLogsCount(0);
    pendingScrollAdjustmentRef.current = 0;
    setSelectedLogIds(new Set());
    setLastSelectedLogIndex(null);
    if (parentRef.current) {
      if (typeof parentRef.current.scrollTo === 'function') {
        parentRef.current.scrollTo({ top: 0, behavior: 'instant' as ScrollBehavior });
      } else {
        parentRef.current.scrollTop = 0;
      }
    }
  }, []);

  return (
    <div className="flex flex-col h-[calc(100vh-45px)] bg-dark-950 select-text overflow-hidden">
      {/* Search & Filter Bar */}
      <LogSearchBar
        filters={filters}
        onFilterChange={setFilters}
        onSearch={loadInitialLogs}
        onReset={handleResetFilters}
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
          <div className="flex items-center gap-1.5 border-r border-dark-700 pr-3 mr-1">
            <button
              onClick={selectAllLogs}
              disabled={logs.length === 0}
              className="px-2 py-1 rounded text-xs font-medium bg-dark-800 text-slate-300 border border-dark-700 hover:border-slate-500 hover:text-white transition disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
              title={logs.length > 200 ? 'Select all logs in buffer (capped at 200)' : 'Select all logs in buffer'}
            >
              Select All
            </button>
            {selectedLogIds.size > 0 && (
              <button
                onClick={deselectAllLogs}
                className="px-2 py-1 rounded text-xs font-medium bg-dark-800 text-slate-400 border border-dark-700 hover:border-slate-500 hover:text-slate-200 transition cursor-pointer"
                title="Deselect all selected logs"
              >
                Deselect All
              </button>
            )}
          </div>

          <span className="font-mono text-slate-400 text-[11px]">
            Screen Buffer: <span className="text-slate-200">{logs.length.toLocaleString()}</span> lines
          </span>

          {filters.to ? (
            <span
              className="flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-medium bg-amber-950/50 text-amber-300 border border-amber-800/70 font-mono select-none"
              title="A historical end-time (To) is set. Live stream is paused to preserve historical boundaries."
            >
              <Clock className="w-3.5 h-3.5 text-amber-400" />
              <span>Historical Range (Stream Paused)</span>
            </span>
          ) : (
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
          )}

          <button
            onClick={clearLogsBuffer}
            title="Clear screen buffer (clears browser view only; does not delete logs from disk)"
            aria-label="Clear screen buffer (clears browser view only; does not delete logs from disk)"
            className="p-1 text-slate-400 hover:text-red-400 hover:bg-dark-800 rounded transition cursor-pointer"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Virtualized Table View */}
      <div className="flex-1 relative overflow-hidden flex flex-col">
        {/* Table Header */}
        <div className="bg-dark-950 border-b border-dark-700 text-slate-400 text-[11px] font-mono font-semibold grid grid-cols-[36px_165px_65px_130px_130px_1fr_60px] px-3 py-1.5 select-none items-center">
          <div className="flex items-center justify-center">
            <button
              onClick={selectedLogIds.size > 0 ? deselectAllLogs : selectAllLogs}
              disabled={logs.length === 0}
              title={selectedLogIds.size > 0 ? 'Deselect all rows' : (logs.length > 200 ? 'Select all rows (capped at 200)' : 'Select all rows')}
              aria-label={selectedLogIds.size > 0 ? 'Toggle deselect all in table' : 'Toggle select all in table'}
              className="p-1 hover:text-slate-200 transition disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
            >
              {selectedLogIds.size > 0 ? (
                <CheckSquare className="w-3.5 h-3.5 text-accent-400" />
              ) : (
                <Square className="w-3.5 h-3.5 opacity-50 hover:opacity-100" />
              )}
            </button>
          </div>
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
                    className={`log-row grid grid-cols-[36px_165px_65px_130px_130px_1fr_60px] px-3 items-center border-b border-dark-900 cursor-pointer text-[11px] leading-tight ${
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
                          onDiagnoseAi([log]);
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

          {/* Infinite Scroll Footer: Loading Spinner or End of Range */}
          {logs.length > 0 && isLoadingMore && (
            <div className="py-2.5 flex items-center justify-center gap-2 text-slate-400 font-mono text-xs bg-dark-950/90 border-t border-dark-900">
              <Loader2 className="w-3.5 h-3.5 animate-spin text-accent-400" />
              <span>Loading older logs...</span>
            </div>
          )}

          {logs.length > 0 && !hasMoreLogs && !isLoadingHistory && (
            <div className="py-2.5 flex items-center justify-center text-slate-500 font-mono text-[11px] bg-dark-950 border-t border-dark-900 select-none">
              — Reached beginning of log history ({logs.length.toLocaleString()} log{logs.length === 1 ? '' : 's'} loaded) —
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
            {(() => {
              const uniqueHosts = Array.from(
                new Set(selectedLogs.map((l) => l.source_alias || l.source_ip || 'unknown'))
              );
              if (uniqueHosts.length === 1) {
                return (
                  <span className="text-xs text-slate-400 font-mono">
                    Host: <span className="text-accent-400">{uniqueHosts[0]}</span>
                  </span>
                );
              }
              return (
                <span className="text-xs text-slate-400 font-mono">
                  Hosts:{' '}
                  <span className="text-accent-400">
                    {uniqueHosts.length} hosts ({uniqueHosts.slice(0, 3).join(', ')}
                    {uniqueHosts.length > 3 ? '...' : ''})
                  </span>
                </span>
              );
            })()}
          </div>

          <div className="flex items-center gap-2">
            {selectedLogs.length > 200 && (
              <span className="text-xs text-amber-400 flex items-center gap-1 font-mono">
                <AlertCircle className="w-3.5 h-3.5 text-amber-400" />
                <span>Max 200 logs for AI</span>
              </span>
            )}

            <button
              onClick={deselectAllLogs}
              className="px-3 py-1 text-xs text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded transition cursor-pointer"
            >
              Deselect All
            </button>

            <button
              onClick={handleLaunchAiAnalysis}
              className={`text-white text-xs font-medium px-4 py-1.5 rounded-lg flex items-center gap-1.5 transition shadow-md cursor-pointer ${
                selectedLogs.length > 200
                  ? 'bg-amber-600 hover:bg-amber-500'
                  : 'bg-accent-600 hover:bg-accent-500'
              }`}
            >
              <Sparkles className="w-3.5 h-3.5" />
              <span>Run Analysis ({selectedLogs.length})</span>
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
          const logsToInspect = ctxLogs && ctxLogs.length > 0 ? ctxLogs : [log];
          setLogs((prevLogs) => {
            const existingIds = new Set(prevLogs.map((l) => l.id));
            const missingLogs = logsToInspect.filter((l) => !existingIds.has(l.id));
            if (missingLogs.length === 0) return prevLogs;
            return [...missingLogs, ...prevLogs].sort((a, b) => {
              const cmp = b.timestamp.localeCompare(a.timestamp);
              return cmp !== 0 ? cmp : b.id - a.id;
            });
          });
          setSelectedLogIds(new Set(logsToInspect.map((l) => l.id)));
          onDiagnoseAi(logsToInspect);
        }}
        onInspectWithContext={(targetAndCtxLogs) => {
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
          onDiagnoseAi(targetAndCtxLogs);
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
