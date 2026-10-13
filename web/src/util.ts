/** Pure helpers (no env, no DB), so they're easy to test. */

/** "2026-10" -> [first day, first day of next month) as ISO dates. */
export function monthRange(month: string): [string, string] {
  const [y, m] = month.split("-").map(Number) as [number, number];
  const next = m === 12 ? `${y + 1}-01` : `${y}-${String(m + 1).padStart(2, "0")}`;
  return [`${month}-01`, `${next}-01`];
}

export function isMonth(value: string | undefined): value is string {
  return !!value && /^\d{4}-(0[1-9]|1[0-2])$/.test(value);
}

/** Only same-site paths, so ?next= can't send someone to another site after signing in. */
export function safeNext(next: string | undefined): string {
  return next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\") ? next : "/";
}
