// Alpine components. Loaded before Alpine itself, so they register on alpine:init.
document.addEventListener("alpine:init", () => {
  const post = (path, body) =>
    fetch(`/api/auth/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}),
      credentials: "same-origin",
    });

  const message = async (res) => {
    const data = await res.json().catch(() => ({}));
    if (res.status === 401 || data.code === "INVALID_EMAIL_OR_PASSWORD") return "Correo o contraseña incorrectos.";
    if (data.code === "USER_ALREADY_EXISTS") return "Ese correo ya tiene cuenta; entra con él.";
    if (data.code === "PASSWORD_TOO_SHORT") return "La contraseña es muy corta.";
    return data.message || `No se pudo (${res.status}).`;
  };

  Alpine.data("authForm", ({ mode, next }) => ({
    name: "",
    email: "",
    password: "",
    error: "",
    busy: false,
    async submit(event) {
      event.preventDefault();
      this.busy = true;
      this.error = "";
      try {
        const res = mode === "sign-up"
          ? await post("sign-up/email", { name: this.name, email: this.email, password: this.password })
          : await post("sign-in/email", { email: this.email, password: this.password });
        if (!res.ok) {
          this.error = await message(res);
          return;
        }
        window.location.assign(next || "/");
      } catch {
        this.error = "No hay conexión con el servidor.";
      } finally {
        this.busy = false;
      }
    },
  }));

  Alpine.data("signOut", () => ({
    async run() {
      await post("sign-out").catch(() => {});
      window.location.assign("/entrar");
    },
  }));
});
