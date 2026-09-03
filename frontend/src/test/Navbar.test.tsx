import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { Navbar } from '../components/common/Navbar.tsx';
import * as systemApi from '../api/system.ts';

vi.mock('../context/AuthContext.tsx', () => ({
  useAuth: () => ({
    logout: vi.fn(),
    isAuthenticated: true,
  }),
}));

describe('Navbar Component', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('navigates to console view when brand logo is clicked', async () => {
    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue({
      status: 'ok',
      database: 'ok',
      queue_depth: 0,
      dropped_logs: 0,
      ingest_rate: 0.0,
    });

    const onTabChange = vi.fn();
    const onSelectTab = vi.fn();

    render(
      <Navbar
        activeTab="settings"
        onTabChange={onTabChange}
        onSelectTab={onSelectTab}
      />
    );

    await waitFor(() => {
      expect(systemApi.fetchHealth).toHaveBeenCalled();
    });

    const brandLogo = screen.getByTitle('Go to Console View');
    fireEvent.click(brandLogo);

    expect(onTabChange).toHaveBeenCalledWith('stream');
    expect(onSelectTab).toHaveBeenCalledWith('console');
  });

  it('does not render LIVE / IDLE indicator and displays live rate when health is fetched', async () => {
    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue({
      status: 'ok',
      database: 'ok',
      queue_depth: 42,
      dropped_logs: 0,
      ingest_rate: 18.5,
    });

    const onTabChange = vi.fn();

    render(
      <Navbar
        activeTab="stream"
        onTabChange={onTabChange}
      />
    );

    // Ensure LIVE / IDLE indicator is absent
    expect(screen.queryByText('LIVE')).toBeNull();
    expect(screen.queryByText('IDLE')).toBeNull();

    // Verify rate, queue, and dropped displays
    await waitFor(() => {
      expect(screen.getByText('18.5 logs/s')).toBeInTheDocument();
      expect(screen.getByText('42')).toBeInTheDocument();
    });
  });
});
