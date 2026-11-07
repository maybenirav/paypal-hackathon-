"use client";

import { AllCommunityModule, ModuleRegistry, colorSchemeDarkBlue, themeQuartz } from "ag-grid-community";

ModuleRegistry.registerModules([AllCommunityModule]);

export const gridTheme = themeQuartz.withPart(colorSchemeDarkBlue).withParams({
  backgroundColor: "#181f30",
  headerBackgroundColor: "#141a29",
  borderColor: "#2a3350",
  accentColor: "#4f8cff",
  fontSize: 13,
});

export const pct = (x: number | null | undefined, digits = 1) =>
  x === null || x === undefined ? "–" : `${(x * 100).toFixed(digits)}%`;
export const money = (x: number | null | undefined, cur = "$") =>
  x === null || x === undefined ? "–" : `${cur}${x.toFixed(2)}`;
