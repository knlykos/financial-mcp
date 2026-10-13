import type { Child } from "hono/jsx";
import type { User } from "../auth";

const NAV = [
  ["/", "Resumen"],
  ["/categorias", "Categorías"],
  ["/movimientos", "Movimientos"],
  ["/reglas", "Reglas"],
] as const;

export function Document(props: { title: string; children: Child }) {
  return (
    <html lang="es">
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>{props.title} · Finanzas</title>
        <link rel="icon" href="/static/icon.svg" type="image/svg+xml" />
        <link rel="stylesheet" href="/static/app.css" />
        <script src="/static/vendor/htmx-2.0.11.min.js" defer></script>
        <script src="/static/app.js" defer></script>
        <script src="/static/vendor/alpine-3.17.4.min.js" defer></script>
      </head>
      {props.children}
    </html>
  );
}

/** Signed-in pages: header + nav. hx-boost turns links and forms into partial page swaps. */
export function Layout(props: { title: string; path: string; user: User; children: Child }) {
  return (
    <Document title={props.title}>
      <body hx-boost="true">
        <header class="topbar" x-data="{ open: false }">
          <a href="/" class="brand">
            <img src="/static/icon.svg" alt="" width="28" height="28" />
            Finanzas
          </a>
          <button class="menu-toggle" type="button" x-on:click="open = !open" aria-label="Menú">☰</button>
          <nav x-bind:class="open && 'open'">
            {NAV.map(([href, label]) => (
              <a href={href} aria-current={props.path === href ? "page" : undefined}>{label}</a>
            ))}
            <span class="user" title={props.user.email}>{props.user.email}</span>
            <button type="button" class="link" x-data="signOut" x-on:click="run">Salir</button>
          </nav>
        </header>
        <main id="main">{props.children}</main>
      </body>
    </Document>
  );
}

/** Month picker that reloads the current page for the chosen month. */
export function MonthPicker(props: { months: string[]; month: string; action: string; label: (m: string) => string }) {
  const options = props.months.includes(props.month) ? props.months : [props.month, ...props.months];
  return (
    <form action={props.action} method="get" class="month-picker" x-data="{}" x-on:change="$el.requestSubmit()">
      <label>
        Mes{" "}
        <select name="mes">
          {options.map((m) => (
            <option value={m} selected={m === props.month}>{props.label(m)}</option>
          ))}
        </select>
      </label>
      <noscript><button>Ver</button></noscript>
    </form>
  );
}
