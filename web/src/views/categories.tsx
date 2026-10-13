import type { CategoryTotal, Kind } from "../db";
import { fmtMoney, KIND_LABEL } from "../format";

const ORDER: (Kind | null)[] = ["income", "expense", "debt", "transfer", null];

export function Categories(props: { month: string; totals: CategoryTotal[] }) {
  const groups = ORDER.map((kind) => ({
    kind,
    rows: props.totals
      .filter((c) => c.kind === kind && (kind !== null || c.count > 0))
      .sort((a, b) => b.count - a.count || Math.abs(b.total) - Math.abs(a.total) || a.name.localeCompare(b.name)),
  })).filter((g) => g.rows.length);

  return (
    <>
      {groups.map((g) => {
        const sum = g.rows.reduce((acc, c) => acc + c.total, 0);
        const max = Math.max(1, ...g.rows.map((c) => Math.abs(c.total)));
        return (
          <section class="panel">
            <h2>
              {g.kind ? KIND_LABEL[g.kind] : "Sin categoría"} <span class="h2-total">{fmtMoney(sum)}</span>
            </h2>
            <table class="table categories">
              <thead><tr><th>Categoría</th><th class="num">Movs.</th><th class="num">Total</th><th class="bar-col" /></tr></thead>
              {g.rows.map((c) => {
                const key = c.id ?? "none";
                return (
                  <tbody x-data="{ open: false }">
                    <tr class={c.count ? "clickable" : "zero"}>
                      <td>
                        {c.count ? (
                          <button
                            type="button" class="link expand"
                            hx-get={`/fragmentos/movimientos?mes=${props.month}&categoria=${key}`}
                            hx-target={`#detail-${key}`} hx-trigger="click once"
                            x-on:click="open = !open" x-bind:aria-expanded="open"
                          >
                            <span class="caret" x-bind:class="open && 'down'">▸</span> {c.name}
                          </button>
                        ) : (
                          <span class="muted">{c.name}</span>
                        )}
                      </td>
                      <td class="num">{c.count}</td>
                      <td class={`num ${c.total < 0 ? "neg" : c.total > 0 ? "pos" : ""}`}>{fmtMoney(c.total)}</td>
                      <td class="bar-col">
                        <span class="bar-track"><span class={`bar ${c.total < 0 ? "neg" : "pos"}`} style={`width:${(Math.abs(c.total) / max) * 100}%`} /></span>
                      </td>
                    </tr>
                    <tr class="detail-row" x-show="open" x-cloak>
                      <td colspan={4}><div id={`detail-${key}`} class="detail"><span class="muted">Cargando…</span></div></td>
                    </tr>
                  </tbody>
                );
              })}
            </table>
          </section>
        );
      })}
    </>
  );
}
