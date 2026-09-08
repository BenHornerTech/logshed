import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { useClipboard, useEscapeKey } from '../utils/hooks.ts';
import * as clipboardModule from '../utils/clipboard.ts';

describe('Custom Hooks (useEscapeKey & useClipboard)', () => {
  describe('useEscapeKey', () => {
    it('calls onClose when isOpen is true and Escape key is pressed', () => {
      const onClose = vi.fn();
      renderHook(() => useEscapeKey(true, onClose));

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('does not call onClose when isOpen is false', () => {
      const onClose = vi.fn();
      renderHook(() => useEscapeKey(false, onClose));

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
      expect(onClose).not.toHaveBeenCalled();
    });

    it('does not call onClose when non-Escape key is pressed', () => {
      const onClose = vi.fn();
      renderHook(() => useEscapeKey(true, onClose));

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
      expect(onClose).not.toHaveBeenCalled();
    });

    it('removes event listener on unmount', () => {
      const onClose = vi.fn();
      const { unmount } = renderHook(() => useEscapeKey(true, onClose));

      unmount();
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
      expect(onClose).not.toHaveBeenCalled();
    });
  });

  describe('useClipboard', () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.restoreAllMocks();
      vi.useRealTimers();
    });

    it('initializes with copied=false', () => {
      const { result } = renderHook(() => useClipboard());
      expect(result.current.copied).toBe(false);
    });

    it('copies text and sets copied=true, resetting after timeout', async () => {
      vi.spyOn(clipboardModule, 'copyToClipboard').mockResolvedValue(true);
      const { result } = renderHook(() => useClipboard(1500));

      let promise: Promise<boolean>;
      await act(async () => {
        promise = result.current.copy('test text');
        await promise;
      });

      expect(clipboardModule.copyToClipboard).toHaveBeenCalledWith('test text');
      expect(result.current.copied).toBe(true);

      act(() => {
        vi.advanceTimersByTime(1499);
      });
      expect(result.current.copied).toBe(true);

      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(result.current.copied).toBe(false);
    });

    it('does not set copied=true if copyToClipboard fails', async () => {
      vi.spyOn(clipboardModule, 'copyToClipboard').mockResolvedValue(false);
      const { result } = renderHook(() => useClipboard());

      await act(async () => {
        await result.current.copy('test text');
      });

      expect(result.current.copied).toBe(false);
    });

    it('handles empty string gracefully without copying', async () => {
      const spy = vi.spyOn(clipboardModule, 'copyToClipboard');
      const { result } = renderHook(() => useClipboard());

      let ok: boolean = true;
      await act(async () => {
        ok = await result.current.copy('');
      });

      expect(ok).toBe(false);
      expect(spy).not.toHaveBeenCalled();
      expect(result.current.copied).toBe(false);
    });

    it('clears timeout on unmount', async () => {
      vi.spyOn(clipboardModule, 'copyToClipboard').mockResolvedValue(true);
      const { result, unmount } = renderHook(() => useClipboard(2000));

      await act(async () => {
        await result.current.copy('test');
      });
      expect(result.current.copied).toBe(true);

      unmount();
      // Should not throw or cause issues when timers advance after unmount
      act(() => {
        vi.advanceTimersByTime(2000);
      });
    });
  });
});
