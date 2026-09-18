import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { NotificationsCard } from '../components/settings/NotificationsCard.tsx';
import * as notifApi from '../api/notifications.ts';

vi.mock('../api/notifications.ts', () => ({
  fetchNotificationChannels: vi.fn(),
  createNotificationChannel: vi.fn(),
  updateNotificationChannel: vi.fn(),
  deleteNotificationChannel: vi.fn(),
  testNotificationTarget: vi.fn(),
}));

describe('NotificationsCard Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders empty state when no channels are configured', async () => {
    vi.mocked(notifApi.fetchNotificationChannels).mockResolvedValueOnce([]);

    render(<NotificationsCard />);

    await waitFor(() => {
      expect(
        screen.getByText(/No notification targets configured/i)
      ).toBeInTheDocument();
    });
    expect(screen.getByText('0 targets')).toBeInTheDocument();
  });

  it('renders configured channels with masked URLs and allows toggling status', async () => {
    vi.mocked(notifApi.fetchNotificationChannels).mockResolvedValueOnce([
      {
        id: 1,
        name: 'Homelab Discord',
        url: 'discord://1...9/a...f',
        is_enabled: true,
        created_at: '2026-09-18T12:00:00Z',
        updated_at: '2026-09-18T12:00:00Z',
      },
    ]);
    vi.mocked(notifApi.updateNotificationChannel).mockResolvedValueOnce({
      id: 1,
      name: 'Homelab Discord',
      url: 'discord://1...9/a...f',
      is_enabled: false,
      created_at: '2026-09-18T12:00:00Z',
      updated_at: '2026-09-18T12:05:00Z',
    });

    render(<NotificationsCard />);

    await waitFor(() => {
      expect(screen.getByText('Homelab Discord')).toBeInTheDocument();
      expect(screen.getByText('discord://1...9/a...f')).toBeInTheDocument();
      expect(screen.getByText('Active')).toBeInTheDocument();
    });

    // Click toggle status
    const statusBtn = screen.getByText('Active');
    fireEvent.click(statusBtn);

    await waitFor(() => {
      expect(notifApi.updateNotificationChannel).toHaveBeenCalledWith(1, { is_enabled: false });
      expect(screen.getByText('Disabled')).toBeInTheDocument();
    });
  });

  it('opens add modal, runs test notification, and creates channel', async () => {
    vi.mocked(notifApi.fetchNotificationChannels).mockResolvedValueOnce([]);
    vi.mocked(notifApi.testNotificationTarget).mockResolvedValueOnce({
      success: true,
      message: 'Notification sent successfully.',
    });
    vi.mocked(notifApi.createNotificationChannel).mockResolvedValueOnce({
      id: 2,
      name: 'Ops Telegram',
      url: 'tgram://1...9/b...z',
      is_enabled: true,
      created_at: '2026-09-18T12:00:00Z',
      updated_at: '2026-09-18T12:00:00Z',
    });

    render(<NotificationsCard />);

    await waitFor(() => {
      expect(screen.getByText('New Target')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('New Target'));

    expect(screen.getByText('Add Notification Target')).toBeInTheDocument();

    const nameInput = screen.getByPlaceholderText(/e.g. Homelab Discord/i);
    const urlInput = screen.getByPlaceholderText(/discord:\/\/webhook_id/i);

    fireEvent.change(nameInput, { target: { value: 'Ops Telegram' } });
    fireEvent.change(urlInput, { target: { value: 'tgram://12345/bot_token' } });

    // Click Send Test Notification
    const testBtn = screen.getByRole('button', { name: /Send Test Notification/i });
    fireEvent.click(testBtn);

    await waitFor(() => {
      expect(notifApi.testNotificationTarget).toHaveBeenCalledWith({ url: 'tgram://12345/bot_token' });
      expect(screen.getByText('Notification sent successfully.')).toBeInTheDocument();
    });

    // Submit modal
    const submitBtn = screen.getByRole('button', { name: /Create Target/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(notifApi.createNotificationChannel).toHaveBeenCalledWith({
        name: 'Ops Telegram',
        url: 'tgram://12345/bot_token',
        is_enabled: true,
      });
      expect(screen.getByText('Ops Telegram')).toBeInTheDocument();
    });
  });
});
