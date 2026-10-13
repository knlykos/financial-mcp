/** Environment, validated once at startup. Secrets come from web/.env (local) or the container env. */

function required(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`Missing environment variable ${name} (see web/README.md)`);
  return value;
}

export const config = {
  port: Number(process.env.PORT ?? 3100),
  databaseUrl: required("DATABASE_URL"),
  authBaseUrl: required("NEON_AUTH_BASE_URL"),
  cookieSecret: required("NEON_AUTH_COOKIE_SECRET"),
  // Only these accounts may sign up or see anything. Neon Auth itself lets anyone register.
  allowedEmails: new Set(
    required("ALLOWED_EMAILS")
      .split(",")
      .map((e) => e.trim().toLowerCase())
      .filter(Boolean),
  ),
};

if (config.cookieSecret.length < 32) throw new Error("NEON_AUTH_COOKIE_SECRET must be at least 32 characters");
