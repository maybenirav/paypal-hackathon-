"use client";

import { AllCommunityModule, ModuleRegistry, colorSchemeLight, themeQuartz } from "ag-grid-community";

ModuleRegistry.registerModules([AllCommunityModule]);

// "Paper" theme to match the scrapbook UI: ink borders, mono headers, ruled rows.
export const gridTheme = themeQuartz.withPart(colorSchemeLight).withParams({
  backgroundColor: "#fffdf8",
  foregroundColor: "#151515",
  headerBackgroundColor: "#f3efe4",
  headerTextColor: "#151515",
  headerFontFamily: "var(--font-mono-stack)",
  headerFontWeight: 700,
  headerFontSize: 11,
  fontFamily: "var(--font-sans-stack)",
  borderColor: "#151515",
  rowBorder: { style: "dashed", width: 1, color: "#d9d4c7" },
  oddRowBackgroundColor: "#fbf9f3",
  accentColor: "#2647d8",
  selectedRowBackgroundColor: "rgba(243, 194, 52, 0.25)",
  rowHoverColor: "rgba(243, 194, 52, 0.18)",
  wrapperBorderRadius: 0,
  borderRadius: 0,
  fontSize: 13,
});

export const pct = (x: number | null | undefined, digits = 1) =>
  x === null || x === undefined ? "–" : `${(x * 100).toFixed(digits)}%`;
export const money = (x: number | null | undefined, cur = "$") =>
  x === null || x === undefined ? "–" : `${cur}${x.toFixed(2)}`;
