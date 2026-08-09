// FASE 11B — POST /api/ingreso
// Registra un ingreso manual (equivale a callback INGRESO| del webhook).
// Body: { tipo: string, monto: number, fecha?: string, pertenece?: string }
// - tipo: categoria de ingreso (debe existir en categorias_fijas con es_ingreso=true)
// - monto: numero > 0
// - fecha: opcional, ISO YYYY-MM-DD (default hoy en zona AR)
// - pertenece: opcional, default "David"

import { requireBearer, supabaseInsert } from "./_supabase.js";

export default async function handler(req, res) {
  if (req.method !== "POST") {
    return res.status(405).json({ error: "Metodo no permitido. Utilizar POST." });
  }
  const auth = requireBearer(req);
  if (!auth.ok) return res.status(auth.status).json({ error: auth.error });

  try {
    const body = req.body;
    if (!body || !body.tipo || body.monto === undefined || body.monto === null) {
      return res.status(400).json({ error: "Faltan campos: tipo, monto" });
    }
    const { tipo, monto, fecha, pertenece = "David" } = body;

    if (typeof tipo !== "string" || !tipo.trim()) {
      return res.status(400).json({ error: "tipo requerido (string no vacio)" });
    }
    const montoNum = Number(monto);
    if (isNaN(montoNum) || montoNum <= 0) {
      return res.status(400).json({ error: "monto debe ser numero > 0" });
    }

    // Validar que la categoria existe y es ingreso activo
    // Nota: usamos supabaseQuery desde _supabase.js
    const { supabaseQuery } = await import("./_supabase.js");
    const categorias = await supabaseQuery(
      "categorias_fijas",
      `?tipo=eq.${encodeURIComponent(tipo.trim())}&es_ingreso=eq.true&activo=eq.true&limit=1`
    );
    if (!categorias.length) {
      return res.status(400).json({ error: `Categoria de ingreso no encontrada o inactiva: ${tipo}` });
    }

    // Fecha: si no se pasa, hoy en zona AR (UTC date string)
    const fechaIso = fecha || new Date().toLocaleDateString("en-CA", { timeZone: "America/Argentina/Cordoba" });
    // en-CA da YYYY-MM-DD

    const row = {
      fecha: fechaIso,
      tipo: tipo.trim(),
      monto: Number(monto),
      origen: "Manual WebApp",
      pertenece: (pertenece || "David").toString(),
    };
    const inserted = await import("./_supabase.js").then(m => m.supabaseInsert("ingresos", row, {
      onConflict: "fecha,tipo,origen",
    }));
    return res.status(201).json({ success: true, data: inserted[0] });
  } catch (error) {
    console.error("[ingreso] error:", error);
    return res.status(500).json({ error: error.message || String(error) });
  }
}