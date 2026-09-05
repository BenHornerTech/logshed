import React, { useState, useRef, useEffect } from 'react';
import { ChevronDown, Check, X, Search, Plus } from 'lucide-react';

export interface MultiSelectDropdownProps {
  label: string;
  options: string[];
  selected: string[];
  onChange: (selected: string[]) => void;
  placeholder?: string;
  icon?: React.ReactNode;
  allowCustomInput?: boolean;
}

export const MultiSelectDropdown: React.FC<MultiSelectDropdownProps> = ({
  label,
  options,
  selected,
  onChange,
  placeholder = 'All',
  icon,
  allowCustomInput = true,
}) => {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const [searchTerm, setSearchTerm] = useState<string>('');
  const containerRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  // Close on click outside
  useEffect(() => {
    const handleOutsideClick = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    if (isOpen) {
      document.addEventListener('mousedown', handleOutsideClick);
    }
    return () => {
      document.removeEventListener('mousedown', handleOutsideClick);
    };
  }, [isOpen]);

  // Focus search input when opened
  useEffect(() => {
    if (isOpen && searchInputRef.current) {
      searchInputRef.current.focus();
    } else {
      setSearchTerm('');
    }
  }, [isOpen]);

  // Handle escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        setIsOpen(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen]);

  const toggleOption = (option: string) => {
    if (selected.includes(option)) {
      onChange(selected.filter((item) => item !== option));
    } else {
      onChange([...selected, option]);
    }
  };

  const handleClear = (e: React.MouseEvent) => {
    e.stopPropagation();
    onChange([]);
  };

  const handleSelectAll = () => {
    const filtered = options.filter((opt) =>
      opt.toLowerCase().includes(searchTerm.toLowerCase())
    );
    const combined = Array.from(new Set([...selected, ...filtered]));
    onChange(combined);
  };

  const handleAddCustom = () => {
    const trimmed = searchTerm.trim();
    if (trimmed && !selected.includes(trimmed)) {
      onChange([...selected, trimmed]);
      setSearchTerm('');
    }
  };

  // Ensure any currently selected items are always included in the list so the user can see and toggle them
  const allOptions = React.useMemo(() => {
    const set = new Set(options);
    selected.forEach((s) => set.add(s));
    return Array.from(set);
  }, [options, selected]);

  // Filter options based on search term
  const filteredOptions = allOptions.filter((opt) =>
    opt.toLowerCase().includes(searchTerm.toLowerCase())
  );

  // Determine button summary text
  const getSummaryText = () => {
    if (selected.length === 0) return placeholder;
    if (selected.length === 1) return selected[0];
    return `${selected[0]} (+${selected.length - 1})`;
  };

  const hasSearchTerm = searchTerm.trim().length > 0;
  const isSearchTermNew =
    hasSearchTerm &&
    !options.some((opt) => opt.toLowerCase() === searchTerm.trim().toLowerCase()) &&
    !selected.some((opt) => opt.toLowerCase() === searchTerm.trim().toLowerCase());

  return (
    <div className="relative inline-block text-left" ref={containerRef}>
      {/* Dropdown Trigger Button */}
      <div
        onClick={() => setIsOpen((prev) => !prev)}
        className={`flex items-center gap-1.5 bg-dark-900 border rounded px-2.5 py-1 text-xs cursor-pointer select-none transition ${
          isOpen
            ? 'border-accent-500 ring-1 ring-accent-500/20'
            : selected.length > 0
            ? 'border-accent-600/60 bg-dark-900/90 text-slate-100'
            : 'border-dark-700 hover:border-slate-600 text-slate-300'
        }`}
        role="button"
        tabIndex={0}
        aria-haspopup="listbox"
        aria-expanded={isOpen}
      >
        {icon}
        <span className="text-slate-400 font-medium text-[11px]">{label}:</span>
        <span
          className={`font-mono text-[11px] truncate max-w-[130px] ${
            selected.length > 0 ? 'text-accent-300 font-medium' : 'text-slate-400'
          }`}
          title={selected.length > 0 ? selected.join(', ') : placeholder}
        >
          {getSummaryText()}
        </span>

        {/* Selected Counter Badge */}
        {selected.length > 1 && (
          <span className="bg-accent-950 text-accent-300 border border-accent-700/60 text-[10px] font-mono px-1 rounded-full font-semibold">
            {selected.length}
          </span>
        )}

        {/* Clear selection 'x' button */}
        {selected.length > 0 && (
          <button
            type="button"
            onClick={handleClear}
            className="text-slate-500 hover:text-slate-200 p-0.5 rounded hover:bg-dark-800 transition ml-0.5"
            title={`Clear ${label.toLowerCase()} filter`}
            aria-label={`Clear ${label.toLowerCase()} filter`}
          >
            <X className="w-3 h-3" />
          </button>
        )}

        <ChevronDown
          className={`w-3 h-3 text-slate-400 transition-transform duration-150 ${
            isOpen ? 'rotate-180 text-accent-400' : ''
          }`}
        />
      </div>

      {/* Popover Dropdown Menu & Click-Away Interceptor */}
      {isOpen && (
        <>
          {/* Fixed transparent backdrop that captures click-aways to prevent triggering underlying elements like log rows */}
          <div
            data-testid="dropdown-backdrop"
            className="fixed inset-0 z-40 bg-transparent"
            onClick={(e) => {
              e.stopPropagation();
              setIsOpen(false);
            }}
          />

          <div className="absolute left-0 mt-1 w-64 bg-dark-900 border border-dark-700 rounded-md shadow-2xl z-50 py-1.5 animate-in fade-in-50 zoom-in-95 duration-100">
          {/* Search Box */}
          <div className="px-2 pb-1.5 border-b border-dark-800">
            <div className="relative">
              <input
                ref={searchInputRef}
                type="text"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && allowCustomInput && isSearchTermNew) {
                    e.preventDefault();
                    handleAddCustom();
                  }
                }}
                placeholder={`Search or add ${label.toLowerCase()}...`}
                className="w-full bg-dark-950 border border-dark-700 rounded px-2 py-1 pl-6 text-[11px] text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
              />
              <Search className="w-3 h-3 text-slate-500 absolute left-2 top-2" />
              {searchTerm && (
                <button
                  type="button"
                  onClick={() => setSearchTerm('')}
                  className="absolute right-1.5 top-1.5 text-slate-500 hover:text-slate-300"
                >
                  <X className="w-3 h-3" />
                </button>
              )}
            </div>
          </div>

          {/* Quick Header Actions */}
          <div className="flex items-center justify-between px-2.5 py-1 text-[10px] text-slate-400 border-b border-dark-800 bg-dark-950/50">
            <span>
              {selected.length} of {options.length} selected
            </span>
            <div className="flex items-center gap-2">
              {filteredOptions.length > 0 && (
                <button
                  type="button"
                  onClick={handleSelectAll}
                  className="hover:text-accent-400 text-slate-400 transition"
                >
                  Select All
                </button>
              )}
              {selected.length > 0 && (
                <button
                  type="button"
                  onClick={() => onChange([])}
                  className="hover:text-amber-400 text-slate-400 transition"
                >
                  Clear
                </button>
              )}
            </div>
          </div>

          {/* Scrollable Option Items */}
          <div className="max-h-[60vh] overflow-y-auto py-1 font-mono text-xs">
            {/* Custom Input Option if typed value not in list */}
            {allowCustomInput && isSearchTermNew && (
              <div
                onClick={handleAddCustom}
                className="flex items-center gap-2 px-2.5 py-1.5 cursor-pointer bg-dark-800/60 hover:bg-accent-950/60 text-accent-300 text-[11px] border-b border-dark-800"
              >
                <Plus className="w-3 h-3 text-accent-400 shrink-0" />
                <span className="truncate">Add "{searchTerm.trim()}"</span>
              </div>
            )}

            {filteredOptions.length === 0 && !isSearchTermNew ? (
              <div className="px-3 py-3 text-center text-slate-500 text-[11px]">
                {options.length === 0 ? `No ${label.toLowerCase()} discovered yet` : 'No matching items'}
              </div>
            ) : (
              filteredOptions.map((option) => {
                const isChecked = selected.includes(option);
                return (
                  <div
                    key={option}
                    onClick={() => toggleOption(option)}
                    className={`flex items-center justify-between px-2.5 py-1 cursor-pointer select-none text-[11px] transition ${
                      isChecked
                        ? 'bg-accent-950/50 text-accent-200'
                        : 'text-slate-300 hover:bg-dark-800 hover:text-slate-100'
                    }`}
                  >
                    <span className="truncate pr-2" title={option}>
                      {option}
                    </span>
                    <div
                      className={`w-3.5 h-3.5 rounded flex items-center justify-center border transition shrink-0 ${
                        isChecked
                          ? 'bg-accent-600 border-accent-500 text-white'
                          : 'border-dark-600 bg-dark-950'
                      }`}
                    >
                      {isChecked && <Check className="w-2.5 h-2.5" />}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </>
    )}
    </div>
  );
};
