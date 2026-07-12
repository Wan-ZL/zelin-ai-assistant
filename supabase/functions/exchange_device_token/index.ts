// exchange_device_token — headless daemon token exchange (plan of record §3).
//
// A headless syncd/actd runs under launchd with no login session, so it cannot
// interactively log in each run. Instead the interactive Mac app logs in ONCE
// and provisions a per-device secret (32 bytes, stored server-side only as
// argon2id(secret) in device_secrets.secret_hash). This function takes that
// secret and mints a SHORT-LIVED (1 hour) JWT carrying a `device_id` claim,
// which the RLS policies use to gate board/heartbeat writes and inbox_actions
// status updates to the owning daemon (see 20260712000100_sync_rls.sql).
//
// We deliberately do NOT hand the daemon a GoTrue user refresh token: that is
// the whole account and cannot be revoked per-device. Per-device revoke =
// set device_secrets.revoked_at (checked below).
//
// ─────────────────────────────────────────────────────────────────────────────
// OPEN QUESTION §8-6 (MUST be resolved/tested before this actually works):
// This mints an HS256 token signed with the project's legacy symmetric JWT
// secret (SUPABASE_JWT_SECRET). That is only accepted by PostgREST/GoTrue if the
// project verifies JWTs with the legacy HS256 shared secret. If the project has
// migrated to ASYMMETRIC JWT signing keys (ES256/RS256 "JWT signing keys"),
// PostgREST will reject a self-signed HS256 token and this exchange is a no-op —
// you would instead need to sign with the project's current asymmetric private
// key (not exposed to Edge Functions the same way) or use a different mint path.
// ACTION: verify the project's JWT config (dashboard → API/Auth → JWT keys)
// against this signing method before relying on it. Untested here by design
// (this is a file-only Phase 1a deliverable; nothing is deployed).
// ─────────────────────────────────────────────────────────────────────────────

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { argon2Verify } from "https://esm.sh/hash-wasm@4.11.0";
import { create } from "https://deno.land/x/djwt@v3.0.2/mod.ts";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
// Legacy HS256 symmetric secret. See the §8-6 caveat above.
const JWT_SECRET = Deno.env.get("SUPABASE_JWT_SECRET") ?? "";

const TOKEN_TTL_SECONDS = 3600; // 1 hour

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

Deno.serve(async (req: Request): Promise<Response> => {
  if (req.method !== "POST") {
    return json(405, { error: "method_not_allowed" });
  }

  let body: { device_id?: string; secret?: string };
  try {
    body = await req.json();
  } catch {
    return json(400, { error: "invalid_json" });
  }

  const deviceId = (body.device_id ?? "").trim();
  const secret = body.secret ?? "";
  if (!deviceId || !secret) {
    return json(400, { error: "device_id_and_secret_required" });
  }

  if (!SUPABASE_URL || !SERVICE_ROLE_KEY || !JWT_SECRET) {
    return json(500, { error: "server_misconfigured" });
  }

  // service_role client bypasses RLS; device_secrets has no policy so this is
  // the ONLY way to read secret_hash.
  const admin = createClient(SUPABASE_URL, SERVICE_ROLE_KEY, {
    auth: { persistSession: false, autoRefreshToken: false },
  });

  const { data, error } = await admin
    .from("device_secrets")
    .select("owner, secret_hash, revoked_at")
    .eq("device_id", deviceId)
    .maybeSingle();

  // Same generic response for unknown/revoked/bad-secret — do not leak which.
  if (error) {
    return json(500, { error: "lookup_failed" });
  }
  if (!data || data.revoked_at) {
    return json(401, { error: "unauthorized" });
  }

  let ok = false;
  try {
    ok = await argon2Verify({ password: secret, hash: data.secret_hash });
  } catch {
    ok = false;
  }
  if (!ok) {
    return json(401, { error: "unauthorized" });
  }

  // Mint the short-lived, device-scoped JWT.
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(JWT_SECRET),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const now = Math.floor(Date.now() / 1000);
  const token = await create(
    { alg: "HS256", typ: "JWT" },
    {
      // sub drives auth.uid() in RLS; role/aud must be "authenticated" so the
      // token maps to the authenticated Postgres role.
      sub: data.owner,
      role: "authenticated",
      aud: "authenticated",
      // custom claim consumed by RLS: auth.jwt()->>'device_id'
      device_id: deviceId,
      iat: now,
      exp: now + TOKEN_TTL_SECONDS,
    },
    key,
  );

  return json(200, {
    access_token: token,
    token_type: "bearer",
    expires_in: TOKEN_TTL_SECONDS,
    device_id: deviceId,
  });
});
