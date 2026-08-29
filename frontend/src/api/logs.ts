import { apiFetch } from './client.ts';
import { LogEntry, LogFilterParams } from '../types.ts';

export interface LogListResult {
  logs: LogEntry[];
  total: number;
  limit: number;
  offset: number;
}

export async function fetchLogs(params: LogFilterParams = {}): Promise<LogListResult> {
  const searchParams = new URLSearchParams();
  if (params.query) searchParams.set('query', params.query);
  if (params.severity_max !== undefined && params.severity_max !== null) {
    searchParams.set('severity_max', params.severity_max.toString());
  }
  if (params.source) searchParams.set('source', params.source);
  if (params.app_name) searchParams.set('app_name', params.app_name);
  if (params.from) searchParams.set('from', params.from);
  if (params.to) searchParams.set('to', params.to);
  if (params.limit !== undefined) searchParams.set('limit', params.limit.toString());
  if (params.offset !== undefined) searchParams.set('offset', params.offset.toString());

  const qs = searchParams.toString();
  return apiFetch<LogListResult>(`/api/logs${qs ? `?${qs}` : ''}`);
}

export async function fetchLogContext(id: number, lines: number = 10): Promise<{ target_id: number; logs: LogEntry[] }> {
  return apiFetch<{ target_id: number; logs: LogEntry[] }>(`/api/logs/${id}/context?lines=${lines}`);
}
