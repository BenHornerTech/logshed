import { useState, useRef, useCallback } from 'react';

export interface PullTouchHandlers {
  onTouchStart: (e: React.TouchEvent) => void;
  onTouchMove: (e: React.TouchEvent) => void;
  onTouchEnd: () => void;
  onTouchCancel: () => void;
}

interface UsePullToRefreshOptions {
  onRefresh: () => Promise<void> | void;
  threshold?: number;
  disabled?: boolean;
}

export function usePullToRefresh({
  onRefresh,
  threshold = 60,
  disabled = false,
}: UsePullToRefreshOptions) {
  const [pullDistance, setPullDistance] = useState<number>(0);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);

  const startYRef = useRef<number | null>(null);
  const isDraggingRef = useRef<boolean>(false);

  const handleTouchStart = useCallback(
    (e: React.TouchEvent) => {
      if (disabled || isRefreshing) return;
      if (e.touches.length !== 1) return;
      startYRef.current = e.touches[0].clientY;
      isDraggingRef.current = true;
    },
    [disabled, isRefreshing]
  );

  const handleTouchMove = useCallback(
    (e: React.TouchEvent) => {
      if (disabled || isRefreshing || !isDraggingRef.current || startYRef.current === null) {
        return;
      }
      const currentY = e.touches[0].clientY;
      const rawDelta = currentY - startYRef.current;

      if (rawDelta > 0) {
        // Apply dampening resistance
        const dampened = Math.min(rawDelta * 0.45, threshold + 25);
        setPullDistance(dampened);
      } else {
        setPullDistance(0);
      }
    },
    [disabled, isRefreshing, threshold]
  );

  const handleTouchEnd = useCallback(async () => {
    if (disabled || isRefreshing || !isDraggingRef.current) return;
    isDraggingRef.current = false;
    startYRef.current = null;

    if (pullDistance >= threshold) {
      setIsRefreshing(true);
      setPullDistance(threshold * 0.7);
      try {
        await Promise.resolve(onRefresh());
      } catch {
        // Refresh errors handled gracefully
      } finally {
        setTimeout(() => {
          setIsRefreshing(false);
          setPullDistance(0);
        }, 400);
      }
    } else {
      setPullDistance(0);
    }
  }, [disabled, isRefreshing, pullDistance, threshold, onRefresh]);

  const hasReachedThreshold = pullDistance >= threshold;

  return {
    pullDistance,
    isPulling: pullDistance > 0,
    hasReachedThreshold,
    isRefreshing,
    touchHandlers: {
      onTouchStart: handleTouchStart,
      onTouchMove: handleTouchMove,
      onTouchEnd: handleTouchEnd,
      onTouchCancel: () => {
        isDraggingRef.current = false;
        startYRef.current = null;
        setPullDistance(0);
      },
    },
  };
}
