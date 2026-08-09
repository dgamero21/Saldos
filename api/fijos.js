// FASE 11B — POST /api/fijos
// ABM de gastos fijos e ingresos fijos (categorias_fijas con monto_fijo).
// Acciones: "crear" | "actualizar" | "eliminar" | "listar"
// Body comunes:
//   { accion: "listar", es_ingreso?: boolean }
//   { accion: "crear", tipo: string, monto_fijo: number, es_ingreso: boolean, pertenece?: string }
//   { accion: "actualizar", id: number, tipo?: string, monto_fijo?: number, activo?: boolean }
//   { accion: "eliminar", id: number }

import { requireBearer, supabaseQuery, supabaseInsert, supabasePatch } from "./_supabase.js";

export default async function handler(req, res) {
  if (req.method !== "POST") {
    return res.status(405).json({ error: "Metodo no permitido. Utilizar POST." });
  }
  const auth = requireBearer(req);
  if (!auth.ok) return res.status(auth.status).json({ error: auth.error });

  try {
    const body = req.body;
    if (!body || !body.accion) {
      return res.status(400).json({ error: "Falta campo: accion" });
    }
    const { accion } = body;

    if (accion === "listar") {
      const es_ingreso = body.es_ingreso === true;
      const query = es_ingreso ? "?es_ingreso=eq.true&order=id.asc" : "?es_ingreso=eq.false&order=id.asc";
      const rows = await supabaseQuery("categorias_fijas", query);
      return res.status(200).json({
        success: true,
        data: rows.map(r => ({
          id: r.id,
          tipo: r.tipo,
          monto_fijo: Number(r.monto_fijo) || null,
          activo: r.activo === true,
          es_ingreso: r.es_ingreso === true,
          pertenece: r.pertenece,
        }))
      });
    }

    if (accion === "crear") {
      const { tipo, monto_fijo, es_ingreso, pertenece = "David" } = body;
      if (!tipo || typeof tipo !== "string" || !tipo.trim()) {
        return res.status(400).json({ error: "tipo requerido (string no vacio)" });
      }
      if (monto_fijo === undefined || monto_fijo === null || isNaN(Number(monto_fijo))) {
        return res.status(400).json({ error: "monto_fijo requerido (numero)" });
      }
      if (typeof es_ingreso !== "boolean") {
        return res.status(400).json({ error: "es_ingreso requerido (boolean)" });
      }

      const row = {
        tipo: tipo.trim(),
        monto_fijo: Number(monto_fijo),
        es_ingreso,
        activo: true,
        pertenece: (pertenece || "David").toString(),
      };
      const inserted = await supabaseInsert("categorias_fijas", row, {
        onConflict: "es_ingreso,tipo",
      });
      return res.status(201).json({ success: true, data: inserted[0] });
    }

    if (accion === "actualizar") {
      const { id, tipo, monto_fijo, activo } = body;
      if (!id || isNaN(Number(id))) {
        return res.status(400).json({ error: "id requerido (numero)" });
      }
      const patch = {};
      if (tipo !== undefined) {
        if (!tipo || !tipo.trim()) return res.status(400).json({ error: "tipo no puede ser vacio" });
        patch.tipo = tipo.trim();
      }
      if (monto_fijo !== undefined) {
        if (isNaN(Number(monto_fijo))) return res.status(400).json({ error: "monto_fijo debe ser numero" });
        patch.monto_fijo = Number(monto_fijo);
      }
      if (activo !== undefined) {
        if (typeof activo !== "boolean") return res.status(400).json({ error: "activo debe ser boolean" });
        patch.activo = activo;
      }
      if (Object.keys(patch).length === 0) {
        return res.status(400).json({ error: "Nada que actualizar" });
      }
      const updated = await supabasePatch("categorias_fijas", `?id=eq.${Number(id)}`, patch);
      return res.status(200).json({ success: true, data: updated[0] });
    }

    if (accion === "eliminar") {
      const { id } = body;
      if (!id || isNaN(Number(id))) {
        return res.status(400).json({ error: "id requerido (numero)" });
      }
      await supabasePatch("categorias_fijas", `?id=eq.${Number(id)}`, { activo: false });
      return res.status(200).json({ success: true, message: "Desactivado (soft delete)" });
    }

    return res.status(400).json({ error: `Accion no valida: ${accion}` });
  } catch (error) {
    console.error("[fijos] error:", error);
    return res.status(500).json({ error: error.message || String(error) });
  }
}