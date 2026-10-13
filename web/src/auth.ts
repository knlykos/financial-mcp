/**
 * Neon Auth (Managed Better Auth) for Hono, following @neondatabase/auth's BUILDING-AN-ADAPTER.md.
 *
 * The browser only talks to this server: /api/auth/* is proxied to Neon Auth, so the session cookies live on
 * our own domain (HttpOnly). On top of Neon Auth, which lets anyone register, this app only lets the emails in
 * ALLOWED_EMAILS sign up or see any page.
 */

import type { Context, MiddlewareHandler } from "hono";
import { getContext } from "hono/context-storage";
import {
  createAuthServer,
  extractNeonAuthCookies,
  handleAuthProxyRequest,
  serializeSetCookie,
  type RequestContext,
} from "@neondatabase/auth/server";
import { config } from "./config";

export type User = { id: string; email: string; name: string };
export type Env = { Variables: { user: User } };

const proxyConfig = {
  baseUrl: config.authBaseUrl,
  cookieSecret: config.cookieSecret,
  // lax: a link to the panel from another site (e.g. a chat) still arrives signed in.
  sameSite: "lax" as const,
};

function honoRequestContext(): RequestContext {
  const c = getContext();
  return {
    getCookies: () => extractNeonAuthCookies(c.req.header("cookie") ?? ""),
    setCookie: (name, value, options) => {
      c.header("Set-Cookie", serializeSetCookie({ name, value, ...options }), { append: true });
    },
    getHeader: (name) => c.req.header(name) ?? null,
    getOrigin: () => c.req.header("origin") ?? new URL(c.req.url).origin,
    getFramework: () => "hono",
  };
}

const server = createAuthServer({ ...proxyConfig, context: honoRequestContext });

// The only Neon Auth endpoints the panel needs; everything else (account deletion, user updates...) stays closed.
const PROXIED = new Set(["sign-in/email", "sign-up/email", "sign-out", "get-session"]);

export function isAllowed(email: string | undefined | null): boolean {
  return !!email && config.allowedEmails.has(email.trim().toLowerCase());
}

/** Mounted at /api/auth/*. */
export async function authProxy(c: Context): Promise<Response> {
  const path = c.req.path.replace(/^\/api\/auth\//, "");
  if (!PROXIED.has(path)) return c.json({ message: "Not found" }, 404);
  if (path === "sign-up/email") {
    const body = (await c.req.raw.clone().json().catch(() => ({}))) as { email?: string };
    if (!isAllowed(body.email)) return c.json({ message: "Este correo no tiene acceso." }, 403);
  }
  return handleAuthProxyRequest({ ...proxyConfig, request: c.req.raw, path });
}

/** Current user from the session cookies (cached in a signed cookie for 5 minutes), or null. */
export async function currentUser(): Promise<User | null> {
  const { data } = await server.getSession();
  const user = data?.user;
  return user ? { id: user.id, email: user.email, name: user.name } : null;
}

/** Protects a route: signed in and on the allowlist, else to the login page (or a 403 for a stranger). */
export const requireUser: MiddlewareHandler<Env> = async (c, next) => {
  const user = await currentUser();
  if (!user) {
    const back = c.req.path === "/" ? "" : `?next=${encodeURIComponent(c.req.path + new URL(c.req.url).search)}`;
    if (c.req.header("HX-Request")) {
      c.header("HX-Redirect", `/entrar${back}`);
      return c.body(null, 401);
    }
    return c.redirect(`/entrar${back}`);
  }
  if (!isAllowed(user.email)) return c.text("Esta cuenta no tiene acceso a este panel.", 403);
  c.set("user", user);
  await next();
};
