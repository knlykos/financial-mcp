import type { Account, Category, Transaction, TxFilter } from "../db";
import { fmtDay, fmtMoney, fmtMonth, KIND_LABEL, SOURCE_LABEL } from "../format";

/** Filters re-render only #tx-results through htmx; the URL keeps the filters so it can be shared or reloaded. */
export function Transactions(props: {
  filter: TxFilter; months: string[]; categories: Category[]; accounts: Account[]; rows: Transaction[];
}) {
  const f = props.filter;
  const kinds = [...new Set(props.categories.map((c) => c.kind))];
  return (
    <>
      <form
        class="filters" action="/movimientos" method="get"
        hx-get="/movimientos" hx-target="#tx-results" hx-select="#tx-results" hx-swap="outerHTML"
        hx-push-url="true" hx-trigger="change, input changed delay:350ms from:find input[name=q]"
      >
        <label>Mes
          <select name="mes">
            <option value="" selected={!f.month}>Todos</option>
            {props.months.map((m) => <option value={m} selected={m === f.month}>{fmtMonth(m)}</option>)}
          </select>
        </label>
        <label>Categoría
          <select name="categoria">
            <option value="">Todas</option>
            <option value="none" selected={f.category === "none"}>(sin categoría)</option>
            {kinds.map((k) => (
              <optgroup label={KIND_LABEL[k]}>
                {props.categories.filter((c) => c.kind === k).map((c) => (
                  <option value={String(c.id)} selected={f.category === c.id}>{c.name}</option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>
        <label>Cuenta
          <select name="cuenta">
            <option value="">Todas</option>
            {props.accounts.map((a) => <option value={a.id} selected={a.id === f.account}>{a.name}</option>)}
          </select>
        </label>
        <label class="grow">Buscar
          <input type="search" name="q" value={f.q ?? ""} placeholder="Comercio o descripción" autocomplete="off" />
        </label>
      </form>
      <TransactionResults rows={props.rows} limit={f.limit ?? 300} />
    </>
  );
}

export function TransactionResults(props: { rows: Transaction[]; limit: number; compact?: boolean }) {
  const total = props.rows.reduce((acc, t) => acc + (t.is_internal_transfer ? 0 : t.amount), 0);
  return (
    <section id="tx-results" class={props.compact ? "" : "panel"}>
      <p class="note">
        {props.rows.length} movimientos{props.rows.length === props.limit ? ` (los ${props.limit} más recientes)` : ""} ·
        total {fmtMoney(total)}{props.compact ? "" : " sin contar transferencias entre tus cuentas"}
      </p>
      {props.rows.length === 0 ? <p class="empty">No hay movimientos con estos filtros.</p> : <TxTable rows={props.rows} compact={props.compact} />}
    </section>
  );
}

function TxTable(props: { rows: Transaction[]; compact?: boolean }) {
  return (
    <div class="table-wrap">
      <table class="table tx">
        <thead>
          <tr>
            <th>Fecha</th><th>Comercio</th>
            {props.compact ? null : <th>Cuenta</th>}
            {props.compact ? null : <th>Categoría</th>}
            <th class="num">Monto</th>
          </tr>
        </thead>
        <tbody>
          {props.rows.map((t) => (
            <tr class={t.is_internal_transfer ? "internal" : ""}>
              <td class="nowrap">{fmtDay(t.date)}</td>
              <td>
                <span title={t.description ?? ""}>{t.payee_normalized || t.description}</span>
                {t.pending ? <span class="badge">pendiente</span> : null}
                {t.is_internal_transfer ? <span class="badge">entre tus cuentas</span> : null}
              </td>
              {props.compact ? null : <td class="sub">{t.account}</td>}
              {props.compact ? null : (
                <td>
                  {t.category ? <span class={`chip ${t.category_kind}`}>{t.category}</span> : <span class="chip none">sin categoría</span>}
                  {t.category_source === "history" || t.category_source === "manual"
                    ? <span class="sub"> {SOURCE_LABEL[t.category_source]}</span>
                    : null}
                </td>
              )}
              <td class={`num nowrap ${t.amount < 0 ? "neg" : "pos"}`}>{fmtMoney(t.amount)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
