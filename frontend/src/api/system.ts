import { apiFetch } from './client.ts';
import { HealthResponse, PruneResponse, StorageMetricsResponse } from '../types.ts';

export async function fetchHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>('/api/health');
}

export async function fetchStorageMetrics(): Promise<StorageMetricsResponse> {
  return apiFetch<StorageMetricsResponse>('/api/system/storage');
}

export async function triggerManualPrune(): Promise<PruneResponse> {
  return apiFetch<PruneResponse>('/api/maintenance/prune', {
    method: 'POST',
  });
}
