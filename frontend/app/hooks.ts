"use client";

import { useEffect, useRef, useState } from "react";

/** Tracks an element's rendered width so charts draw at real pixels instead of scaling text. */
export function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Measure now as well: ResizeObserver doesn't fire until the page renders, so a chart mounted
    // in a background tab would otherwise stay blank.
    setWidth(el.clientWidth);
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}
