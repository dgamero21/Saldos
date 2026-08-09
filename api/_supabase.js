// Helpers compartidos Supabase (PostgREST) + auth bearer para endpoints Vercel.
// FASE 11A: reusado por api/dashboard.js. No se exporta como ruta (prefijo _).

export function getEnv(name, fallback = "") {
  const v = process.env[name];
  return v === undefined ? fallback : v;
}

// Token estático (single-user). Compara con timingSafeEqual para evitar
// timing leaks. El secreto vive en Vercel env (API_APP_TOKEN).
export function requireBearer(req) {
  const expected = (process.env.API_APP_TOKEN || "").trim();
  if (!expected) {
    return { ok: false, status: 500, error: "API_APP_TOKEN no configurado" };
  }
  // Headers case-insensitive: buscar 'authorization' o 'Authorization'
  const authHeader = req.headers["authorization"] || req.headers["Authorization"] || "";
  const m = /^Bearer\s+(.+)$/i.exec(authHeader);
  const provided = m ? m[1].trim() : "";
  if (!provided) return { ok: false, status: 401, error: "Token requerido" };
  if (provided.length !== expected.length) return { ok: false, status: 401, error: "Token invalido" };
  let diff = 0;
  for (let i = 0; i < provided.length; i++) {
    diff |= provided.charCodeAt(i) ^ expected.charCodeAt(i);
  }
  return diff === 0
    ? { ok: true }
    : { ok: false, status: 401, error: "Token invalido" };
}

export function supabaseConfig() {
  const url = (process.env.SUPABASE_URL || "").replace(/\/$/, "");
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY || "";
  if (!url || !key) {
    throw new Error("Supabase no configurado en Vercel (faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY).");
  }
  return { url, key };
}

export async function supabaseQuery(table, query = "") {
  const { url, key } = supabaseConfig();
  const headers = {
    apikey: key,
    Authorization: `Bearer ${key}`,
    Accept: "application/json",
  };
  const response = await fetch(`${url}/rest/v1/${table}${query}`, { headers });
  const text = await response.text();
  const isJson = (response.headers.get("content-type") || "").includes("application/json");
  const data = text ? (isJson ? JSON.parse(text) : text) : null;
  if (!response.ok) {
    const detail = typeof data === "string" ? data : JSON.stringify(data);
    throw new Error(`Supabase HTTP ${response.status}: ${detail}`);
  }
  return Array.isArray(data) ? data : (data ? [data] : []);
}

// Formatea una fecha ISO (YYYY-MM-DD) o Date a dd/MM/yyyy (igual que
// sanitizarString del Code.gs original, basado en zona horaria del script).
// Usamos UTC para stabiliadad; el dashboard agrupa por anio/mes del lado JS.
export function toArDate(iso) {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (m) return `${m[3]}/${m[2]}/${m[1]}`;
  return String(iso);
}

export function iconoPorEntidad(entidad) {
  const e = (entidad || "").toUpperCase();
  if (e === "BNA") return "account_balance";
  if (e === "EPEC") return "bolt";
  if (e.includes("HIPOTECARIO")) return "local_fire_department";
  return "credit_card";
}
