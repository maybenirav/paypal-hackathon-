import type { ReactNode } from "react";
import type { StatusKind } from "./format";

const PATHS = {
  plus: <path d="M12 5v14M5 12h14" />,
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  stop: <rect x="6" y="6" width="12" height="12" rx="1.5" />,
  alert: <><path d="M12 4 2.8 20h18.4L12 4z" /><path d="M12 10v4.5M12 17.5v.01" /></>,
  play: <path d="M8 5.5l11 6.5-11 6.5v-13z" />,
  pause: <path d="M8.5 5v14M15.5 5v14" />,
  moon: <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z" />,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4" /></>,
  external: <path d="M14 5h5v5M19 5l-8.5 8.5M10 6H6a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1v-4" />,
  refresh: <path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4v4h-4" />,
  send: <path d="M5 12h13M12.5 6.5 18 12l-5.5 5.5" />,
  close: <path d="M6.5 6.5l11 11M17.5 6.5l-11 11" />,
  flask: <path d="M9.5 3.5h5M10.5 3.5v6.2L5.2 18a1.7 1.7 0 0 0 1.5 2.6h10.6a1.7 1.7 0 0 0 1.5-2.6l-5.3-8.3V3.5M8 15h8" />,
  dot: <circle cx="12" cy="12" r="3.5" fill="currentColor" />,
} satisfies Record<string, ReactNode>;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {PATHS[name]}
    </svg>
  );
}

const KIND_ICON: Record<StatusKind, IconName> = {
  neutral: "dot", note: "dot", good: "check", crit: "stop", warn: "alert",
};

/** Status always pairs its colour with an icon and a word, never colour alone. */
export function Pill({ kind, children, icon }: { kind: StatusKind; children: ReactNode; icon?: IconName }) {
  return (
    <span className={`pill pill-${kind}`}>
      <Icon name={icon ?? KIND_ICON[kind]} size={13} />
      {children}
    </span>
  );
}
