import React from 'react';
import { X } from 'lucide-react';
import { useEscapeKey } from '../../utils/hooks.ts';

interface SlideOverProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  width?: string;
}

export const SlideOver: React.FC<SlideOverProps> = ({
  isOpen,
  onClose,
  title,
  children,
  width = 'max-w-3xl',
}) => {
  useEscapeKey(isOpen, onClose);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 overflow-hidden">
      {/* Backdrop overlay */}
      <div
        data-testid="slideover-backdrop"
        className="fixed inset-0 bg-black/60 backdrop-blur-xs transition-opacity cursor-pointer"
        onClick={onClose}
        aria-hidden="true"
      />
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="fixed inset-y-0 right-0 flex max-w-full pl-10">
          <div
            className={`pointer-events-auto w-screen ${width} bg-dark-900 border-l border-dark-700 shadow-2xl flex flex-col animate-in slide-in-from-right duration-200`}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-dark-700 bg-dark-950">
              <h3 className="text-sm font-semibold text-slate-200 tracking-wide">{title}</h3>
              <button
                onClick={onClose}
                className="text-slate-400 hover:text-slate-200 hover:bg-dark-800 p-1 rounded transition"
                aria-label="Close panel"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Body */}
            <div className="p-4 overflow-y-auto flex-1">{children}</div>
          </div>
        </div>
      </div>
    </div>
  );
};
