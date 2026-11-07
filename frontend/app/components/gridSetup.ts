import {
  CellStyleModule, ClientSideRowModelModule, ColumnAutoSizeModule, ModuleRegistry, PaginationModule,
  RowStyleModule, themeQuartz, TooltipModule, ValidationModule,
} from "ag-grid-community";

// Only what the two grids use. In development, ValidationModule reports any feature missing from this list.
ModuleRegistry.registerModules([
  ClientSideRowModelModule, ColumnAutoSizeModule, PaginationModule, TooltipModule,
  CellStyleModule, RowStyleModule,
  ...(process.env.NODE_ENV === "production" ? [] : [ValidationModule]),
]);

/** AG Grid draws from the same CSS tokens as the rest of the app, so it follows the theme toggle. */
export const gridTheme = themeQuartz.withParams({
  browserColorScheme: "inherit",
  backgroundColor: "var(--surface)",
  foregroundColor: "var(--ink)",
  textColor: "var(--ink)",
  headerBackgroundColor: "var(--surface)",
  headerTextColor: "var(--ink-3)",
  borderColor: "var(--line)",
  rowHoverColor: "var(--surface-2)",
  accentColor: "var(--focus)",
  fontFamily: "var(--font-sans)",
  fontSize: 13,
  headerFontSize: 12,
  headerFontWeight: 500,
  rowHeight: 46,
  headerHeight: 38,
  cellHorizontalPaddingScale: 0.7,
  borderRadius: 4,
  wrapperBorder: false,
  wrapperBorderRadius: 0,
  columnBorder: false,
  headerColumnBorder: false,
});
