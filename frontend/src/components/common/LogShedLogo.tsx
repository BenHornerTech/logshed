import React from 'react';

interface LogShedLogoProps {
  className?: string;
  shedClassName?: string;
  barClassName?: string;
}

export const LogShedLogo: React.FC<LogShedLogoProps> = ({
  className = 'w-6 h-6',
  shedClassName = 'fill-accent-500',
  barClassName = 'fill-dark-950',
}) => {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 32 32"
      className={className}
      aria-hidden="true"
    >
      {/* Gable Roof Shed Silhouette */}
      <path
        className={shedClassName}
        d="M16 2.5 L3 12.5 V28 C3 29.1 3.9 30 5 30 H27 C28.1 30 29 29.1 29 28 V12.5 Z"
      />

      {/* Stacked Log Lines / Data Streams */}
      <rect className={barClassName} x="10" y="14" width="12" height="3" rx="1.5" />
      <rect className={barClassName} x="7" y="19" width="18" height="3" rx="1.5" />
      <rect className={barClassName} x="7" y="24" width="18" height="3" rx="1.5" />
    </svg>
  );
};
