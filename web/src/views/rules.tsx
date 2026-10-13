import type { Rule } from "../db";

const SOURCE: Record<string, string> = { user: "tuya", local: "personal", seed: "genérica" };
const DIRECTION: Record<string, string> = { any: "ambas", in: "entradas", out: "salidas" };
const FIELD: Record<string, string> = {
  payee_normalized: "comercio", payee: "payee", description: "descripción", memo: "memo",
};

/** Filtered in the browser with Alpine: every rule is already on the page. */
export function Rules(props: { rules: Rule[] }) {
  return (
    <section class="panel" x-data="{ q: '', unused: false }">
      <div class="filters">
        <label class="grow">Buscar
          <input type="search" x-model="q" placeholder="Patrón o categoría" autocomplete="off" />
        </label>
        <label class="check"><input type="checkbox" x-model="unused" /> Solo las que no han categorizado nada</label>
      </div>
      <p class="note">
        Se aplican en orden de prioridad (número menor primero). «Movs.» es cuántos movimientos categorizó esa regla.
      </p>
      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr><th class="num">Prior.</th><th>Origen</th><th>Campo</th><th>Patrón</th><th>Dirección</th><th>Categoría</th><th class="num">Movs.</th></tr>
          </thead>
          <tbody>
            {props.rules.map((r) => (
              <tr
                data-search={`${r.pattern} ${r.category}`.toLowerCase()}
                x-show={`(!q || $el.dataset.search.includes(q.toLowerCase())) && (!unused || ${r.transactions} === 0)`}
              >
                <td class="num">{r.priority}</td>
                <td><span class={`chip src-${r.source}`}>{SOURCE[r.source] ?? r.source}</span></td>
                <td class="sub">{FIELD[r.match_field] ?? r.match_field} · {r.match_type}</td>
                <td><code>{r.pattern}</code></td>
                <td class="sub">{DIRECTION[r.direction] ?? r.direction}</td>
                <td>{r.category}</td>
                <td class={`num ${r.transactions ? "" : "muted"}`}>{r.transactions}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
