/** Read-only queries over the MCP server's tables. Connects as `finanzas_web`, which only has SELECT. */

import postgres from "postgres";
import { config } from "./config";
import { monthRange } from "./util";

export { isMonth, monthRange } from "./util";

export const sql = postgres(config.databaseUrl, { max: 5, idle_timeout: 30, connect_timeout: 20 });

export type Kind = "income" | "expense" | "transfer" | "debt";

export async function months(): Promise<string[]> {
  const rows = await sql<{ month: string }[]>`
    SELECT DISTINCT to_char(date, 'YYYY-MM') AS month FROM transactions ORDER BY month DESC`;
  return rows.map((r) => r.month);
}

export type Account = {
  id: string; name: string; institution: string | null; kind: "depository" | "credit";
  balance: number | null; available_balance: number | null; balance_date: string | null; synced_at: string | null;
};

export function accounts() {
  return sql<Account[]>`
    SELECT id, name, institution, kind, balance, available_balance, balance_date::text, synced_at::text
    FROM accounts ORDER BY kind DESC, institution, name`;
}

export type Summary = {
  income: number; expenses: number; debt: number; transfers: number;
  uncategorized_count: number; uncategorized_total: number; count: number;
};

/** Totals for a month by category kind. Transfers between the user's own accounts are left out. */
export async function summary(month: string): Promise<Summary> {
  const [start, end] = monthRange(month);
  const [row] = await sql<Summary[]>`
    SELECT
      COALESCE(SUM(t.amount) FILTER (WHERE c.kind = 'income'), 0)::float8 AS income,
      COALESCE(SUM(t.amount) FILTER (WHERE c.kind = 'expense'), 0)::float8 AS expenses,
      COALESCE(SUM(t.amount) FILTER (WHERE c.kind = 'debt'), 0)::float8 AS debt,
      COALESCE(SUM(t.amount) FILTER (WHERE c.kind = 'transfer'), 0)::float8 AS transfers,
      COUNT(*) FILTER (WHERE t.category_id IS NULL)::int AS uncategorized_count,
      COALESCE(SUM(t.amount) FILTER (WHERE t.category_id IS NULL), 0)::float8 AS uncategorized_total,
      COUNT(*)::int AS count
    FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
    WHERE t.date >= ${start} AND t.date < ${end} AND NOT t.is_internal_transfer`;
  return row!;
}

export type CategoryTotal = { id: number | null; name: string; kind: Kind | null; count: number; total: number };

/** Every category with its count and total for the month (zeros included), plus uncategorized last. */
export async function categoryTotals(month: string): Promise<CategoryTotal[]> {
  const [start, end] = monthRange(month);
  return sql<CategoryTotal[]>`
    SELECT c.id, c.name, c.kind, COUNT(t.id)::int AS count, COALESCE(SUM(t.amount), 0)::float8 AS total
    FROM categories c
    LEFT JOIN transactions t ON t.category_id = c.id AND t.date >= ${start} AND t.date < ${end}
         AND NOT t.is_internal_transfer
    GROUP BY c.id
    UNION ALL
    SELECT NULL, '(sin categoría)', NULL, COUNT(*)::int, COALESCE(SUM(amount), 0)::float8
    FROM transactions WHERE category_id IS NULL AND date >= ${start} AND date < ${end} AND NOT is_internal_transfer
    ORDER BY 2`;
}

export type MonthTrend = { month: string; income: number; spending: number };

/** Income and spending (expense + debt categories) for the last `n` months that have data. */
export function trend(n = 6) {
  return sql<MonthTrend[]>`
    SELECT * FROM (
      SELECT to_char(t.date, 'YYYY-MM') AS month,
             COALESCE(SUM(t.amount) FILTER (WHERE c.kind = 'income'), 0)::float8 AS income,
             COALESCE(-SUM(t.amount) FILTER (WHERE c.kind IN ('expense', 'debt')), 0)::float8 AS spending
      FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
      WHERE NOT t.is_internal_transfer
      GROUP BY 1 ORDER BY 1 DESC LIMIT ${n}
    ) m ORDER BY month`;
}

export type Category = { id: number; name: string; kind: Kind };

export function categories() {
  return sql<Category[]>`SELECT id, name, kind FROM categories ORDER BY kind, name`;
}

export type Transaction = {
  id: string; date: string; amount: number; payee_normalized: string; description: string | null;
  account: string; category: string | null; category_kind: Kind | null; category_source: string;
  pending: boolean; is_internal_transfer: boolean;
};

export type TxFilter = {
  month?: string;
  category?: number | "none";
  account?: string;
  q?: string;
  limit?: number;
};

export function transactions(f: TxFilter) {
  const [start, end] = f.month ? monthRange(f.month) : [null, null];
  const like = f.q ? `%${f.q}%` : null;
  return sql<Transaction[]>`
    SELECT t.id, t.date::text, t.amount, t.payee_normalized, t.description, a.name AS account,
           c.name AS category, c.kind AS category_kind, t.category_source, t.pending, t.is_internal_transfer
    FROM transactions t JOIN accounts a ON a.id = t.account_id LEFT JOIN categories c ON c.id = t.category_id
    WHERE TRUE
      ${start ? sql`AND t.date >= ${start} AND t.date < ${end}` : sql``}
      ${f.category === "none" ? sql`AND t.category_id IS NULL AND NOT t.is_internal_transfer` : sql``}
      ${typeof f.category === "number" ? sql`AND t.category_id = ${f.category}` : sql``}
      ${f.account ? sql`AND t.account_id = ${f.account}` : sql``}
      ${like ? sql`AND (t.payee_normalized ILIKE ${like} OR t.description ILIKE ${like})` : sql``}
    ORDER BY t.date DESC, t.amount
    LIMIT ${f.limit ?? 300}`;
}

export type Rule = {
  id: number; match_field: string; match_type: string; pattern: string; direction: string;
  category: string; priority: number; source: string; transactions: number;
};

export function rules() {
  return sql<Rule[]>`
    SELECT r.id, r.match_field, r.match_type, r.pattern, r.direction, c.name AS category, r.priority, r.source,
           COUNT(t.id)::int AS transactions
    FROM category_rules r JOIN categories c ON c.id = r.category_id
    LEFT JOIN transactions t ON t.category_rule_id = r.id
    GROUP BY r.id, c.name ORDER BY r.priority, r.id`;
}
