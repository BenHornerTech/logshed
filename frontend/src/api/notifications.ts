import { apiFetch } from './client.ts';

export async function testNotifications(): Promise<{ status: string; detail?: string }> {
  return apiFetch<{ status: string; detail?: string }>('/api/notifications/test', {
    method: 'POST',
  });
}

export async function sendPushoverNotification(payload: { title: string; message: string; priority?: number }): Promise<{ status: string }> {
  return apiFetch<{ status: string }>('/api/notifications/pushover', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}
