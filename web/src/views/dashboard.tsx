import type { Account, CategoryTotal, MonthTrend, Summary } from "../db";
import { daysSince, fmtDay, fmtMoney, fmtMonth, fmtShortMonth } from "../format";

export function Dashboard(props: {
  month: string; summary: Summary; spending: CategoryTotal[]; trend: MonthTrend[]; accounts: Account[];
}) {
  const s = props.summary;
  const net = s.income + s.expenses + s.debt;
  const maxSpend = Math.max(1, ...props.spending.map((c) => -c.total));
  const maxTrend = Math.max(1, ...props.trend.flatMap((t) => [t.income, t.spending]));
  const q = (extra: string) => `/movimientos?mes=${props.month}&${extra}`;

  return (
    <>
      <section class="cards">
        <Card label="Ingresos" value={fmtMoney(s.income)} tone="pos" />
        <Card label="Gastos" value={fmtMoney(s.expenses)} tone="neg" />
        <Card label="Deudas y tarjetas" value={fmtMoney(s.debt)} tone="neg" />
        <Card label="Neto del mes" value={fmtMoney(net)} tone={net >= 0 ? "pos" : "neg"} />
        <a class="card" href={q("categoria=none")}>
          <span class="card-label">Sin categoría</span>
          <span class={`card-value ${s.uncategorized_count ? "warn" : ""}`}>{s.uncategorized_count}</span>
          <span class="card-note">{fmtMoney(s.uncategorized_total)}</span>
        </a>
      </section>
      <p class="note">
        No cuenta las transferencias entre tus propias cuentas. Transferencias a otras personas:{" "}
        {fmtMoney(s.transfers)}.
      </p>

      <div class="grid-2">
        <section class="panel">
          <h2>Gasto por categoría</h2>
          {props.spending.length === 0 ? (
            <p class="empty">Sin gastos este mes.</p>
          ) : (
            <ul class="bars">
              {props.spending.map((c) => (
                <li>
                  <a href={q(`categoria=${c.id}`)}>
                    <span class="bar-label">{c.name}</span>
                    <span class="bar-track"><span class="bar neg" style={`width:${(-c.total / maxSpend) * 100}%`} /></span>
                    <span class="bar-value">{fmtMoney(-c.total)}</span>
                  </a>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section class="panel">
          <h2>Últimos meses</h2>
          <div class="trend" role="img" aria-label="Ingresos y gastos por mes">
            {props.trend.map((t) => (
              <a class="trend-month" href={`/?mes=${t.month}`} title={`${fmtMonth(t.month)}: ingresos ${fmtMoney(t.income)}, gastos ${fmtMoney(t.spending)}`}>
                <span class="trend-bars">
                  <span class="trend-bar pos" style={`height:${(t.income / maxTrend) * 100}%`} />
                  <span class="trend-bar neg" style={`height:${(t.spending / maxTrend) * 100}%`} />
                </span>
                <span class={`trend-label ${t.month === props.month ? "current" : ""}`}>{fmtShortMonth(t.month)}</span>
              </a>
            ))}
          </div>
          <p class="legend"><span class="dot pos" /> Ingresos <span class="dot neg" /> Gastos y deudas</p>
        </section>
      </div>

      <section class="panel">
        <h2>Cuentas</h2>
        <table class="table">
          <thead>
            <tr><th>Cuenta</th><th>Tipo</th><th class="num">Saldo</th><th class="num">Disponible</th><th>Actualizado</th></tr>
          </thead>
          <tbody>
            {props.accounts.map((a) => {
              const age = daysSince(a.balance_date);
              return (
                <tr>
                  <td><a href={`/movimientos?cuenta=${encodeURIComponent(a.id)}`}>{a.name}</a><div class="sub">{a.institution}</div></td>
                  <td>{a.kind === "credit" ? "Tarjeta" : "Débito"}</td>
                  <td class={`num ${(a.balance ?? 0) < 0 ? "neg" : ""}`}>{fmtMoney(a.balance)}</td>
                  <td class="num">{a.available_balance == null ? <span class="muted">sin dato</span> : fmtMoney(a.available_balance)}</td>
                  <td class={age != null && age > 3 ? "warn" : ""}>
                    {a.balance_date ? fmtDay(a.balance_date) : "—"}
                    {age != null && age > 3 ? <div class="sub">hace {age} días</div> : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
    </>
  );
}

function Card(props: { label: string; value: string; tone: "pos" | "neg" }) {
  return (
    <div class="card">
      <span class="card-label">{props.label}</span>
      <span class={`card-value ${props.tone}`}>{props.value}</span>
    </div>
  );
}
