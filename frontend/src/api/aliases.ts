import { apiFetch } from './client.ts';
import { HostAlias } from '../types.ts';

export async function fetchAliases(): Promise<HostAlias[]> {
  return apiFetch<HostAlias[]>('/api/aliases');
}

export async function saveAlias(aliasData: { ip: string; alias: string; notes?: string | null }): Promise<HostAlias> {
  return apiFetch<HostAlias>('/api/aliases', {
    method: 'POST',
    body: JSON.stringify(aliasData),
  });
}

export async function deleteAlias(ip: string): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/api/aliases/${encodeURIComponent(ip)}`, {
    method: 'DELETE',
  });
}
