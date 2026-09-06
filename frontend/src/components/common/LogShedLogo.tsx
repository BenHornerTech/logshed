import React from 'react';

interface LogShedLogoProps {
  className?: string;
  frameClassName?: string;
  shedClassName?: string;
  barClassName?: string;
}

export const LogShedLogo: React.FC<LogShedLogoProps> = ({
  className = 'w-6 h-6 text-accent-500',
  frameClassName,
  shedClassName,
  barClassName,
}) => {
  const frameClass = frameClassName || shedClassName || 'stroke-current';
  const barClass = barClassName || 'fill-current';

  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 32 32"
      className={className}
      aria-hidden="true"
    >
      {/* Variation A: Sides and Gable Roof (No bottom line) */}
      <path
        className={frameClass}
        d="M4 27.5 V12.5 L16 3 L28 12.5 V27.5"
        fill="none"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />

      {/* Stacked Log Lines / Data Streams */}
      <rect className={barClass} x="10" y="13.5" width="12" height="2.75" rx="1.375" />
      <rect className={barClass} x="7.5" y="18.5" width="17" height="2.75" rx="1.375" />
      <rect className={barClass} x="7.5" y="23.5" width="17" height="2.75" rx="1.375" />
    </svg>
  );
};
