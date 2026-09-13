import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { App, pathToTab, tabToPath } from '../App.tsx';
import * as settingsApi from '../api/settings.ts';
import * as aiApi from '../api/ai.ts';
import * as systemApi from '../api/system.ts';

vi.mock('../context/AuthContext.tsx', () => ({
  useAuth: () => ({
    isAuthenticated: true,
    setupRequired: false,
    isLoading: false,
    logout: vi.fn(),
  }),
}));

vi.mock('../components/logs/LiveLogStream.tsx', () => ({
  LiveLogStream: () => <div data-testid="live-log-stream">Console View Content</div>,
}));

vi.mock('../components/aliases/HostAliasManager.tsx', () => ({
  HostAliasManager: () => <div data-testid="host-alias-manager">Host Alias Content</div>,
}));

vi.mock('../components/storage/StoragePanel.tsx', () => ({
  StoragePanel: () => <div data-testid="storage-panel">Storage Content</div>,
}));

describe('URL Routing and History API Synchronization', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue({
      status: 'ok',
      db: 'ok',
      database: 'ok',
      queue_depth: 0,
      dropped_logs: 0,
      ingest_rate: 0.0,
    });

    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({
      ai_provider: 'gemini',
      ai_model: 'gemini-3.7-flash',
      ai_api_key: '********',
      ai_base_url: null,
      internal_log_level: 'WARNING',
      retention_days: 14,
      max_retention_days: 30,
      retention_overridden: false,
      has_ai_api_key: true,
    });

    vi.spyOn(aiApi, 'getAiModels').mockResolvedValue({
      provider: 'gemini',
      models: [
        { id: 'gemini-3.7-flash', name: 'Gemini 3.7 Flash', supports_thinking: true },
      ],
      has_api_key: true,
      cached_at: null,
      is_live: true,
    });
  });

  afterEach(() => {
    window.history.pushState(null, '', '/');
  });

  describe('Route helper mapping', () => {
    it('maps pathname to corresponding AppTab', () => {
      expect(pathToTab('/')).toBe('stream');
      expect(pathToTab('/console')).toBe('stream');
      expect(pathToTab('/aliases')).toBe('aliases');
      expect(pathToTab('/aliases/')).toBe('aliases');
      expect(pathToTab('/storage')).toBe('storage');
      expect(pathToTab('/settings')).toBe('settings');
      expect(pathToTab('/unknown-path')).toBe('stream');
    });

    it('maps AppTab to canonical URL path', () => {
      expect(tabToPath('stream')).toBe('/');
      expect(tabToPath('aliases')).toBe('/aliases');
      expect(tabToPath('storage')).toBe('/storage');
      expect(tabToPath('settings')).toBe('/settings');
    });
  });

  describe('App history synchronization', () => {
    it('initializes on settings tab when URL pathname is /settings', async () => {
      window.history.pushState(null, '', '/settings');
      render(<App />);

      await waitFor(() => {
        expect(screen.getByText('System Configuration')).toBeInTheDocument();
      });
      expect(screen.queryByTestId('live-log-stream')).toBeNull();
    });

    it('initializes on storage tab when URL pathname is /storage', async () => {
      window.history.pushState(null, '', '/storage');
      render(<App />);

      await waitFor(() => {
        expect(screen.getByTestId('storage-panel')).toBeInTheDocument();
      });
    });

    it('initializes on aliases tab when URL pathname is /aliases', async () => {
      window.history.pushState(null, '', '/aliases');
      render(<App />);

      await waitFor(() => {
        expect(screen.getByTestId('host-alias-manager')).toBeInTheDocument();
      });
    });

    it('updates URL pathname when user clicks navigation tabs', async () => {
      window.history.pushState(null, '', '/');
      const pushStateSpy = vi.spyOn(window.history, 'pushState');

      render(<App />);
      expect(screen.getByTestId('live-log-stream')).toBeInTheDocument();

      // Click Host Aliases tab
      const aliasesBtn = screen.getByRole('button', { name: /^Host Aliases$/i });
      await act(async () => {
        fireEvent.click(aliasesBtn);
      });

      expect(pushStateSpy).toHaveBeenCalledWith(null, '', '/aliases');
      expect(screen.getByTestId('host-alias-manager')).toBeInTheDocument();

      // Click Storage tab
      const storageBtn = screen.getByRole('button', { name: /^Storage$/i });
      await act(async () => {
        fireEvent.click(storageBtn);
      });

      expect(pushStateSpy).toHaveBeenCalledWith(null, '', '/storage');
      expect(screen.getByTestId('storage-panel')).toBeInTheDocument();

      // Click Brand logo to return to console stream
      const brandLogo = screen.getByTitle('Go to Console View');
      await act(async () => {
        fireEvent.click(brandLogo);
      });

      expect(pushStateSpy).toHaveBeenCalledWith(null, '', '/');
      expect(screen.getByTestId('live-log-stream')).toBeInTheDocument();
    });

    it('switches tabs on popstate event (browser back and forward navigation)', async () => {
      window.history.pushState(null, '', '/');
      render(<App />);
      expect(screen.getByTestId('live-log-stream')).toBeInTheDocument();

      // Simulate user navigating to /storage via browser back/forward
      act(() => {
        window.history.pushState(null, '', '/storage');
        window.dispatchEvent(new PopStateEvent('popstate'));
      });

      await waitFor(() => {
        expect(screen.getByTestId('storage-panel')).toBeInTheDocument();
      });

      // Simulate user navigating to /aliases
      act(() => {
        window.history.pushState(null, '', '/aliases');
        window.dispatchEvent(new PopStateEvent('popstate'));
      });

      await waitFor(() => {
        expect(screen.getByTestId('host-alias-manager')).toBeInTheDocument();
      });
    });
  });
});
