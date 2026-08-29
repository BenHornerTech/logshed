import { apiFetch } from './client.ts';
import { SystemSettings } from '../types.ts';

export interface SettingsResponseData extends SystemSettings {
  has_ai_api_key: boolean;
  has_pushover_user_key: boolean;
  has_pushover_app_token: boolean;
}

export async function fetchSettings(): Promise<SettingsResponseData> {
  return apiFetch<SettingsResponseData>('/api/settings');
}

export async function updateSettings(settings: Partial<SystemSettings>): Promise<{ status: string }> {
  return apiFetch<{ status: string }>('/api/settings', {
    method: 'POST',
    body: JSON.stringify(settings),
  });
}
