export interface Column {
  key: string;
  label: string;
  // numbers are right-aligned with tabular figures
  align?: "left" | "right" | "center";
  width?: number | string;
  minWidth?: number | string;
  // a class on every cell of the column (header and body), for a view's own rule
  className?: string;
}
