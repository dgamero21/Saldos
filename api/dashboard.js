// FASE 11A — GET /api/dashboard
// Reproduce getDashboardRawData() del Code.gs original contra PostgREST.
// Solo lectura. Auth bearer (API_APP_TOKEN).
import { requireBearer, supabaseQuery, toArDate, iconoPorEntidad } from "./_supabase.js";

export default async function handler(req, res) {
  if (req.method !== "GET") {
    return res.status(405).json({ error: "Metodo no permitido. Utilizar GET." });
  }
  const auth = requireBearer(req);
  if (!auth.ok) return res.status(auth.status).json({ error: auth.error });

  try {
    // mapaEntidades: remitente (lower) -> entidad (de reglas). El Code.gs usa
    // la hoja Datos (col 0 remitente, col 11 entidad); aqui equivalente a
    // SELECT remitente, entidad FROM reglas WHERE entidad IS NOT NULL.
    let reglas = [];
    try {
      reglas = await supabaseQuery(
        "reglas",
        "?select=remitente,entidad&entidad=not.is.null"
      );
    } catch (e) {
      // Si reglas falla, el mapeo queda vacio (fallback a heuristicas como Code.gs).
      reglas = [];
    }
    const mapaEntidades = {};
    for (const r of reglas) {
      const rem = (r.remitente || "").trim().toLowerCase();
      const ent = (r.entidad || "").trim();
      if (rem) mapaEntidades[rem] = ent || "Otros";
    }

    const entidadPara = (remitenteRaw) => {
      const rem = (remitenteRaw || "").trim().toLowerCase();
      const ent = mapaEntidades[rem];
      if (ent) return ent;
      if (rem.includes("telegram") || rem.includes("manual")) return "Manual Telegram";
      if (rem.includes("bna")) return "BNA";
      if (rem.includes("epec")) return "EPEC";
      if (rem.includes("naranja")) return "NARANJA";
      return "Otros";
    };

    // --- Consumos ---
    // Columnas: fecha_consumo, comprobante, detalle, cuota_actual, cuota_total,
    // pesos, dolar, fecha_vencimiento, remitente, pertenece (id_consumo opcional).
    // El filtro por defecto es id.asc (igual que Code.gs DataRange).
    const consumosRows = await supabaseQuery(
      "consumos",
      "?select=fecha_consumo,comprobante,detalle,cuota_actual,cuota_total,pesos,dolar,fecha_vencimiento,remitente,pertenece&order=id.asc&limit=5000"
    );

    const consumos = [];
    const vencimientosManuales = [];
    for (const r of consumosRows) {
      const fechaConsumoStr = r.fecha_consumo ? toArDate(r.fecha_consumo) : "";
      if (!fechaConsumoStr) continue;
      const fechaObj = r.fecha_consumo ? new Date(r.fecha_consumo + "T00:00:00Z") : null;
      const remitenteRaw = r.remitente || "";
      const remitenteLow = remitenteRaw.toLowerCase();
      const pertenece = (r.pertenece || "David").toString();
      const entidad = entidadPara(remitenteRaw);

      consumos.push({
        rowId: r._rowid ?? null, // PostgREST no expone rowid por defecto; mantengo null
        fecha: fechaConsumoStr,
        anio: fechaObj ? fechaObj.getUTCFullYear() : null,
        mes: fechaObj ? fechaObj.getUTCMonth() + 1 : null,
        comprobante: r.comprobante || "",
        detalle: (r.detalle || "").toString(),
        cuotaActual: Number(r.cuota_actual) || 1,
        cuotaTotal: Number(r.cuota_total) || 1,
        monto: Number(r.pesos) || 0,
        fechaVencimiento: r.fecha_vencimiento ? toArDate(r.fecha_vencimiento) : fechaConsumoStr,
        remitente: remitenteRaw,
        entidad,
        pertenece,
      });

      if (remitenteLow.includes("manual") || remitenteLow.includes("telegram")) {
        vencimientosManuales.push({
          entidad: "Manual: " + (r.detalle || ""),
          asunto: "Gasto Manual Telegram",
          monto: Number(r.pesos) || 0,
          fechaVencimiento: fechaConsumoStr,
          anio: fechaObj ? fechaObj.getUTCFullYear() : null,
          mes: fechaObj ? fechaObj.getUTCMonth() + 1 : null,
          linkDrive: "",
          pertenece,
          icono: "payments",
          // FASE 11A (decision a aprobada): manuales historicos siempre
          // PENDIENTE (la migracion FASE 2 convirtio dolar='PAGADO' a 0.00,
          // perdiendo el marcador; el estado manual no se reconstruye aqui).
          estado: "PENDIENTE",
          rowId: null,
          hojaOrigen: "consumos",
        });
      }
    }

    // --- Ingresos ---
    const ingresosRows = await supabaseQuery(
      "ingresos",
      "?select=fecha,tipo,monto,pertenece&order=id.asc&limit=5000"
    );
    const ingresos = [];
    for (const r of ingresosRows) {
      const fechaStr = r.fecha ? toArDate(r.fecha) : "";
      if (!fechaStr) continue;
      const fechaObj = r.fecha ? new Date(r.fecha + "T00:00:00Z") : null;
      ingresos.push({
        fecha: fechaStr,
        anio: fechaObj ? fechaObj.getUTCFullYear() : null,
        mes: fechaObj ? fechaObj.getUTCMonth() + 1 : null,
        tipo: (r.tipo || "").toString(),
        monto: Number(r.monto) || 0,
        pertenece: (r.pertenece || "David").toString(),
      });
    }

    // --- Consolidado -> vencimientos ---
    const consolidadoRows = await supabaseQuery(
      "consolidado",
      "?select=remitente,asunto,monto_total,fecha_vencimiento,link_drive,pagado,pertenece&order=id.asc&limit=2000"
    );
    const vencimientos = [];
    for (const r of consolidadoRows) {
      const remitenteRaw = r.remitente || "";
      const fechaVencStr = r.fecha_vencimiento ? toArDate(r.fecha_vencimiento) : "";
      if (!fechaVencStr) continue;
      const fechaObj = r.fecha_vencimiento ? new Date(r.fecha_vencimiento + "T00:00:00Z") : null;
      vencimientos.push({
        entidad: entidadPara(remitenteRaw),
        asunto: (r.asunto || "").toString(),
        monto: Number(r.monto_total) || 0,
        fechaVencimiento: fechaVencStr,
        anio: fechaObj ? fechaObj.getUTCFullYear() : null,
        mes: fechaObj ? fechaObj.getUTCMonth() + 1 : null,
        linkDrive: (r.link_drive || "").toString(),
        pertenece: (r.pertenece || "David").toString(),
        // pagado BOOLEAN (FASE 10A) -> estado PAGADO/PENDIENTE.
        estado: r.pagado === true ? "PAGADO" : "PENDIENTE",
        rowId: null,
        hojaOrigen: "Consolidado",
        icono: iconoPorEntidad(entidadPara(remitenteRaw)),
      });
    }

    return res.status(200).json({
      consumos,
      ingresos,
      vencimientos: vencimientos.concat(vencimientosManuales),
      error: null,
    });
  } catch (error) {
    console.error("[dashboard] error:", error);
    return res.status(200).json({
      consumos: [],
      ingresos: [],
      vencimientos: [],
      error: error.message || String(error),
    });
  }
}
