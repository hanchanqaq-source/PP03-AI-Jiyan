export const money = (value: number | null | undefined, digits = 2) => value == null
  ? "—"
  : new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: digits }).format(value);

export const number = (value: number | null | undefined, digits = 4) => value == null
  ? "—"
  : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: digits }).format(value);

export const percent = (value: number | null | undefined, digits = 2) => value == null
  ? "—"
  : `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;

export const dateTime = (value: string | null | undefined) => value
  ? value.replace("T", " ").replace(/\+08:00$/, "")
  : "—";
