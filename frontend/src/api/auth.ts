import { apiFetch } from './client.ts';
import { AuthStatusResponse } from '../types.ts';

export async function getAuthStatus(): Promise<AuthStatusResponse> {
  return apiFetch<AuthStatusResponse>('/api/auth/status');
}

export async function setupAdmin(password: string): Promise<{ status: string }> {
  return apiFetch<{ status: string }>('/api/auth/setup', {
    method: 'POST',
    body: JSON.stringify({ password }),
  });
}

export async function loginAdmin(password: string): Promise<{ status: string }> {
  return apiFetch<{ status: string }>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ password }),
  });
}

export async function logoutAdmin(): Promise<{ status: string }> {
  return apiFetch<{ status: string }>('/api/auth/logout', {
    method: 'POST',
  });
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<{ status: string }> {
  return apiFetch<{ status: string }>('/api/auth/password', {
    method: 'POST',
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
}
