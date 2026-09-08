import { useState, useEffect, useRef, useCallback } from 'react';
import { copyToClipboard } from './clipboard.ts';

export interface UseClipboardReturn {
  copied: boolean;
  copy: (text: string) => Promise<boolean>;
}

/**
 * Reusable clipboard hook that manages copied state and timeout cleanup.
 */
export function useClipboard(timeoutMs: number = 2000): UseClipboardReturn {
  const [copied, setCopied] = useState<boolean>(false);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const copy = useCallback(
    async (text: string): Promise<boolean> => {
      if (!text) return false;
      const ok = await copyToClipboard(text);
      if (ok) {
        if (timeoutRef.current !== null) {
          clearTimeout(timeoutRef.current);
        }
        setCopied(true);
        timeoutRef.current = setTimeout(() => {
          setCopied(false);
          timeoutRef.current = null;
        }, timeoutMs);
      }
      return ok;
    },
    [timeoutMs]
  );

  useEffect(() => {
    return () => {
      if (timeoutRef.current !== null) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  return { copied, copy };
}

/**
 * Hook to attach a keydown listener on `window` when `isOpen` is true,
 * invoking `onClose` when Escape is pressed and cleaning up properly.
 */
export function useEscapeKey(isOpen: boolean, onClose: () => void): void {
  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen, onClose]);
}
