import type { Kind } from "./db";

const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const monthName = new Intl.DateTimeFormat("es-MX", { month: "long", year: "numeric", timeZone: "UTC" });
const shortMonth = new Intl.DateTimeFormat("es-MX", { month: "short", timeZone: "UTC" });
const dayMonth = new Intl.DateTimeFormat("es-MX", { day: "numeric", month: "short", timeZone: "UTC" });

export const fmtMoney = (n: number | null | undefined) => (n == null ? "—" : money.format(n));

/** "2026-10" -> "octubre 2026" */
export const fmtMonth = (m: string) => monthName.format(new Date(`${m}-01T00:00:00Z`));

/** "2026-10" -> "oct" */
export const fmtShortMonth = (m: string) => shortMonth.format(new Date(`${m}-01T00:00:00Z`)).replace(".", "");

/** "2026-10-12" -> "12 oct" */
export const fmtDay = (d: string) => dayMonth.format(new Date(`${d}T00:00:00Z`)).replace(".", "");

export const currentMonth = () => new Date().toISOString().slice(0, 7);

export const KIND_LABEL: Record<Kind, string> = {
  income: "Ingresos",
  expense: "Gastos",
  debt: "Deudas y tarjetas",
  transfer: "Transferencias",
};

export const SOURCE_LABEL: Record<string, string> = {
  rule: "por regla",
  history: "por historial",
  manual: "a mano",
  none: "sin categoría",
};

/** Days since an ISO date (for stale balances). */
export const daysSince = (d: string | null) =>
  d ? Math.floor((Date.now() - new Date(`${d.slice(0, 10)}T00:00:00Z`).getTime()) / 86_400_000) : null;
