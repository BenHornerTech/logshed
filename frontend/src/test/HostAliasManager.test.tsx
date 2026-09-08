import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { HostAliasManager } from '../components/aliases/HostAliasManager.tsx';
import * as aliasesApi from '../api/aliases.ts';

describe('HostAliasManager Component', () => {
  const mockAliases = [
    {
      ip: '192.168.1.1',
      alias: 'router.local',
      notes: 'Main router',
      created_at: '2026-09-01T10:00:00Z',
    },
    {
      ip: '192.168.1.50',
      alias: 'proxmox-01',
      notes: null,
      created_at: '2026-09-02T11:00:00Z',
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(aliasesApi, 'fetchAliases').mockResolvedValue(mockAliases);
  });

  it('renders active host aliases in the table', async () => {
    render(<HostAliasManager />);

    await waitFor(() => {
      expect(screen.getByText('Active Host Mappings (2)')).toBeInTheDocument();
    });

    expect(screen.getByText('192.168.1.1')).toBeInTheDocument();
    expect(screen.getByText('router.local')).toBeInTheDocument();
    expect(screen.getByText('Main router')).toBeInTheDocument();
    expect(screen.getByText('192.168.1.50')).toBeInTheDocument();
    expect(screen.getByText('proxmox-01')).toBeInTheDocument();
  });

  it('disables delete button while deletion is pending to prevent double-click race conditions', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    let resolveDelete: (val: any) => void;
    const deletePromise = new Promise((resolve) => {
      resolveDelete = resolve;
    });
    const deleteSpy = vi.spyOn(aliasesApi, 'deleteAlias').mockImplementation(() => deletePromise as any);

    render(<HostAliasManager />);

    await waitFor(() => {
      expect(screen.getByText('192.168.1.1')).toBeInTheDocument();
    });

    const deleteButtons = screen.getAllByTitle('Delete Alias');
    const firstDeleteBtn = deleteButtons[0];

    expect(firstDeleteBtn).not.toBeDisabled();

    // Click delete
    fireEvent.click(firstDeleteBtn);

    // Button should now be disabled and marked with disabled styling
    expect(firstDeleteBtn).toBeDisabled();
    expect(firstDeleteBtn.className).toContain('disabled:opacity-50');
    expect(firstDeleteBtn.className).toContain('disabled:cursor-not-allowed');

    // Attempting second click should not trigger another deleteAlias call
    fireEvent.click(firstDeleteBtn);
    expect(deleteSpy).toHaveBeenCalledTimes(1);

    // Resolve deletion
    resolveDelete!({ status: 'deleted', ip: '192.168.1.1' });

    await waitFor(() => {
      expect(firstDeleteBtn).not.toBeDisabled();
    });
  });
});
