// FASE 11B — POST /api/pago
// Marca un vencimiento como PAGADO o PENDIENTE.
// Body: { hojaOrigen: "Consolidado"|"consumos", rowId: number, estado: "PAGADO"|"PENDIENTE" }
// - Consolidado: actualiza consolidado.pagado (boolean) por id (rowId = id)
// - consumos:   actualiza consumos.dolar = NULL (PENDIENTE) o 0 (PAGADO)  -- legacy marker
//   NOTA: en migración FASE 2 se convirtió dolar='PAGADO' a 0.00. Aquí usamos
//   dolar=0 como PAGADO, dolar=NULL como PENDIENTE. No hay col. estado en consumos.

import { requireBearer, supabasePatch } from "./_supabase.js";

export default async function handler(req, res) {
  if (req.method !== "POST") {
    return res.status(405).json({ error: "Metodo no permitido. Utilizar POST." });
  }
  const auth = requireBearer(req);
  if (!auth.ok) return res.status(auth.status).json({ error: auth.error });

  try {
    const body = req.body;
    if (!body || !body.hojaOrigen || !body.rowId || !body.estado) {
      return res.status(400).json({ error: "Faltan campos: hojaOrigen, rowId, estado" });
    }
    const { hojaOrigen, rowId, estado } = body;
    if (!["PAGADO", "PENDIENTE"].includes(estado)) {
      return res.status(400).json({ error: "estado debe ser PAGADO o PENDIENTE" });
    }
    if (!["Consolidado", "consumos"].includes(hojaOrigen)) {
      return res.status(400).json({ error: "hojaOrigen debe ser Consolidado o consumos" });
    }

    if (hojaOrigen === "Consolidado") {
      // consolidado.pagado es BOOLEAN (FASE 10A)
      const pagado = estado === "PAGADO";
      await supabasePatch(
        "consolidado",
        `?id=eq.${rowId}`,
        { pagado }
      );
    } else {
      // consumos: usamos dolar como marcador legacy (0 = PAGADO, NULL = PENDIENTE)
      // NOTA: dolar es NUMERIC(14,2). Usamos NULL para PENDIENTE, 0 para PAGADO.
      const dolarValue = estado === "PAGADO" ? 0 : null;
      await supabasePatch(
        "consumos",
        `?id=eq.${rowId}`,
        { dolar: dolarValue }
      );
    }

    return res.status(200).json({ success: true, hojaOrigen, rowId, estado });
  } catch (error) {
    console.error("[pago] error:", error);
    return res.status(500).json({ error: error.message || String(error) });
  }
}