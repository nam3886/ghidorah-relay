export interface Column {
  key: string;
  label: string;
  // numbers are right-aligned with tabular figures
  align?: "left" | "right" | "center";
  width?: number | string;
  minWidth?: number | string;
  // a class on every cell of the column (header and body), for a view's own rule
  className?: string;
  // the bar the loading skeleton draws in this column; by position when left out (time, title, pills, net)
  skeleton?: "time" | "title" | "text" | "pill" | "net";
}
