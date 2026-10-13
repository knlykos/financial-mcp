import { Hono } from "hono";
import { serveStatic } from "hono/bun";
import { contextStorage } from "hono/context-storage";
import { secureHeaders } from "hono/secure-headers";
import { fileURLToPath } from "node:url";
import { authProxy, currentUser, isAllowed, requireUser, type Env } from "./auth";
import { config } from "./config";
import * as db from "./db";
import { currentMonth, fmtMonth } from "./format";
import { safeNext } from "./util";
import { AuthPage } from "./views/auth";
import { Categories } from "./views/categories";
import { Dashboard } from "./views/dashboard";
import { Layout, MonthPicker } from "./views/layout";
import { Rules } from "./views/rules";
import { TransactionResults, Transactions } from "./views/transactions";

const app = new Hono<Env>();

app.use(contextStorage());
app.use(
  secureHeaders({
    contentSecurityPolicy: {
      defaultSrc: ["'self'"],
      // Alpine evaluates its x-* expressions with new Function().
      scriptSrc: ["'self'", "'unsafe-eval'"],
      styleSrc: ["'self'", "'unsafe-inline'"],
      imgSrc: ["'self'", "data:"],
      connectSrc: ["'self'"],
      formAction: ["'self'"],
      frameAncestors: ["'none'"],
      baseUri: ["'none'"],
    },
    referrerPolicy: "same-origin",
  }),
);

app.get("/health", (c) => c.text("ok"));

// --- Static files ---------------------------------------------------------------------------------
const vendor = (pkgPath: string) => Bun.file(fileURLToPath(new URL(`../node_modules/${pkgPath}`, import.meta.url)));
const VENDOR: Record<string, string> = {
  "htmx-2.0.11.min.js": "htmx.org/dist/htmx.min.js",
  "alpine-3.17.4.min.js": "alpinejs/dist/cdn.min.js",
};
app.get("/static/vendor/:file", (c) => {
  const file = VENDOR[c.req.param("file")];
  if (!file) return c.notFound();
  return new Response(vendor(file), {
    headers: { "Content-Type": "text/javascript; charset=utf-8", "Cache-Control": "public, max-age=31536000, immutable" },
  });
});
app.use("/static/*", serveStatic({ root: fileURLToPath(new URL("../public", import.meta.url)), rewriteRequestPath: (p) => p.replace(/^\/static/, "") }));

// --- Auth -----------------------------------------------------------------------------------------
app.all("/api/auth/*", authProxy);


for (const [path, mode] of [["/entrar", "sign-in"], ["/registro", "sign-up"]] as const) {
  app.get(path, async (c) => {
    const user = await currentUser();
    if (user && isAllowed(user.email)) return c.redirect(safeNext(c.req.query("next")));
    return c.html(<AuthPage mode={mode} next={safeNext(c.req.query("next"))} />);
  });
}

// --- Pages (signed in) ----------------------------------------------------------------------------
const pages = new Hono<Env>();
pages.use(requireUser);

const pickMonth = (value: string | undefined) => (db.isMonth(value) ? value : currentMonth());

pages.get("/", async (c) => {
  const month = pickMonth(c.req.query("mes"));
  const [months, summary, totals, trend, accounts] = await Promise.all([
    db.months(), db.summary(month), db.categoryTotals(month), db.trend(6), db.accounts(),
  ]);
  const spending = totals
    .filter((t) => (t.kind === "expense" || t.kind === "debt") && t.total < 0)
    .sort((a, b) => a.total - b.total);
  return c.html(
    <Layout title="Resumen" path="/" user={c.get("user")}>
      <div class="page-head">
        <h1>{fmtMonth(month)}</h1>
        <MonthPicker months={months} month={month} action="/" label={fmtMonth} />
      </div>
      <Dashboard month={month} summary={summary} spending={spending} trend={trend} accounts={accounts} />
    </Layout>,
  );
});

pages.get("/categorias", async (c) => {
  const month = pickMonth(c.req.query("mes"));
  const [months, totals] = await Promise.all([db.months(), db.categoryTotals(month)]);
  return c.html(
    <Layout title="Categorías" path="/categorias" user={c.get("user")}>
      <div class="page-head">
        <h1>Categorías · {fmtMonth(month)}</h1>
        <MonthPicker months={months} month={month} action="/categorias" label={fmtMonth} />
      </div>
      <Categories month={month} totals={totals} />
    </Layout>,
  );
});

function parseFilter(q: Record<string, string>): db.TxFilter {
  const category = q.categoria === "none" ? "none" : /^\d+$/.test(q.categoria ?? "") ? Number(q.categoria) : undefined;
  return {
    month: db.isMonth(q.mes) ? q.mes : undefined,
    category,
    account: q.cuenta || undefined,
    q: q.q?.trim().slice(0, 100) || undefined,
  };
}

pages.get("/movimientos", async (c) => {
  const query = c.req.query();
  // Default to this month, unless the link already narrows it (a category or an account).
  const filter = parseFilter({ mes: query.categoria || query.cuenta || "mes" in query ? "" : currentMonth(), ...query });
  const [months, cats, accounts, rows] = await Promise.all([
    db.months(), db.categories(), db.accounts(), db.transactions(filter),
  ]);
  return c.html(
    <Layout title="Movimientos" path="/movimientos" user={c.get("user")}>
      <div class="page-head"><h1>Movimientos</h1></div>
      <Transactions filter={filter} months={months} categories={cats} accounts={accounts} rows={rows} />
    </Layout>,
  );
});

/** Loaded by htmx into an expanded category on /categorias. */
pages.get("/fragmentos/movimientos", async (c) => {
  const filter = { ...parseFilter(c.req.query()), limit: 100 };
  const rows = await db.transactions(filter);
  return c.html(<TransactionResults rows={rows} limit={100} compact />);
});

pages.get("/reglas", async (c) => {
  const rules = await db.rules();
  return c.html(
    <Layout title="Reglas" path="/reglas" user={c.get("user")}>
      <div class="page-head"><h1>Reglas de categorización</h1></div>
      <Rules rules={rules} />
    </Layout>,
  );
});

app.route("/", pages);

app.onError((err, c) => {
  console.error(err);
  return c.text("Algo salió mal. Revisa los logs del servidor.", 500);
});

console.log(`Finanzas panel on http://localhost:${config.port}`);
export default { port: config.port, fetch: app.fetch };
