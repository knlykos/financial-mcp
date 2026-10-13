import { Document } from "./layout";

/** Sign-in / sign-up. The form posts JSON to /api/auth/* (Alpine `authForm` in public/app.js). */
export function AuthPage(props: { mode: "sign-in" | "sign-up"; next: string }) {
  const signUp = props.mode === "sign-up";
  return (
    <Document title={signUp ? "Crear cuenta" : "Entrar"}>
      <body class="auth-page">
        <main class="auth-card" x-data={`authForm(${JSON.stringify({ mode: props.mode, next: props.next })})`}>
          <img src="/static/icon.svg" alt="" width="48" height="48" />
          <h1>{signUp ? "Crear cuenta" : "Finanzas"}</h1>
          <p class="muted">{signUp ? "Solo para el correo autorizado." : "Entra para ver tus cuentas y categorías."}</p>
          <form x-on:submit="submit" method="post">
            {signUp ? (
              <label>Nombre<input name="name" x-model="name" autocomplete="name" required /></label>
            ) : null}
            <label>Correo<input name="email" type="email" x-model="email" autocomplete="email" required autofocus /></label>
            <label>
              Contraseña
              <input
                name="password" type="password" x-model="password" required minlength={signUp ? 12 : undefined}
                autocomplete={signUp ? "new-password" : "current-password"}
              />
            </label>
            <p class="error" x-show="error" x-text="error" x-cloak />
            <button type="submit" class="primary" x-bind:disabled="busy">
              <span x-show="!busy">{signUp ? "Crear cuenta" : "Entrar"}</span>
              <span x-show="busy" x-cloak>Un momento…</span>
            </button>
          </form>
          <p class="switch">
            {signUp ? <a href="/entrar">Ya tengo cuenta</a> : <a href="/registro">Crear cuenta</a>}
          </p>
        </main>
      </body>
    </Document>
  );
}
