export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Método no permitido. Utilizar POST.' });
  }

  const TELEGRAM_TOKEN = process.env.TELEGRAM_TOKEN;
  const TELEGRAM_CHAT_ID = process.env.TELEGRAM_CHAT_ID;
  const SUPABASE_URL = (process.env.SUPABASE_URL || '').replace(/\/$/, '');
  const SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || '';

  // FASE 7: NO retirar todavía estas variables de producción.
  const SHEET_ID = process.env.SHEET_ID;
  const GOOGLE_CLIENT_ID = process.env.GOOGLE_CLIENT_ID;
  const GOOGLE_CLIENT_SECRET = process.env.GOOGLE_CLIENT_SECRET;
  const GOOGLE_REFRESH_TOKEN = process.env.GOOGLE_REFRESH_TOKEN;

  // Auxiliar para enviar mensaje a Telegram
  const sendTelegram = async (text, replyMarkup = null) => {
    try {
      const url = `https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage`;
      const body = { chat_id: TELEGRAM_CHAT_ID, text };
      if (replyMarkup) body.reply_markup = replyMarkup;
      await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
    } catch (e) {
      console.error("[ERROR sendTelegram]:", e);
    }
  };

  // Auxiliar para editar mensaje de Telegram
  const editTelegram = async (messageId, text) => {
    try {
      const url = `https://api.telegram.org/bot${TELEGRAM_TOKEN}/editMessageText`;
      await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: TELEGRAM_CHAT_ID, message_id: messageId, text })
      });
    } catch (e) {
      console.error("[ERROR editTelegram]:", e);
    }
  };

  const ensureSupabase = () => {
    if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY) {
      throw new Error('Supabase no configurado en Vercel (faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY).');
    }
  };

  const supabaseFetch = async (path, options = {}) => {
    ensureSupabase();
    const headers = {
      apikey: SUPABASE_SERVICE_ROLE_KEY,
      Authorization: `Bearer ${SUPABASE_SERVICE_ROLE_KEY}`,
      ...(options.headers || {})
    };
    const response = await fetch(`${SUPABASE_URL}${path}`, {
      method: options.method || 'GET',
      headers,
      body: options.body
    });
    const text = await response.text();
    const isJson = (response.headers.get('content-type') || '').includes('application/json');
    const data = text ? (isJson ? JSON.parse(text) : text) : null;
    if (!response.ok) {
      const detail = typeof data === 'string' ? data : JSON.stringify(data);
      throw new Error(`Supabase HTTP ${response.status}: ${detail}`);
    }
    return data;
  };

  const supabaseQuery = async (table, query = '') => {
    return await supabaseFetch(`/rest/v1/${table}${query}`, {
      headers: { Accept: 'application/json' }
    }) || [];
  };

  const supabaseInsert = async (table, rows, { onConflict = '' } = {}) => {
    const qs = onConflict ? `?on_conflict=${encodeURIComponent(onConflict)}` : '';
    return await supabaseFetch(`/rest/v1/${table}${qs}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Prefer: 'resolution=ignore-duplicates,return=minimal'
      },
      body: JSON.stringify(Array.isArray(rows) ? rows : [rows])
    });
  };

  const toArDate = (value) => {
    if (!value) return '';
    if (/^\d{2}\/\d{2}\/\d{4}$/.test(value)) return value;
    if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
      const [y, m, d] = value.split('-');
      return `${d}/${m}/${y}`;
    }
    return value;
  };

  const monthYearAr = (value) => {
    const ar = toArDate(value);
    const m = ar.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
    return m ? `${parseInt(m[2], 10)}/${m[3]}` : '';
  };

  const todayAr = () => new Date().toLocaleDateString('es-AR', {
    timeZone: 'America/Argentina/Cordoba',
    day: '2-digit', month: '2-digit', year: 'numeric'
  });

  const todayIso = () => {
    const [d, m, y] = todayAr().split('/');
    return `${y}-${m}-${d}`;
  };

  const obtenerCategorias = async () => {
    const rows = await supabaseQuery(
      'categorias_fijas',
      // FASE 10A: solo categorías activas (activo = TRUE) en el teclado de
      // Telegram. Las desactivadas se ocultan, no se borran.
      '?select=es_ingreso,tipo&activo=eq.true&order=id.asc'
    );
    const tiposGastos = [];
    const tiposIngresos = [];
    rows.forEach((r) => {
      const tipo = (r.tipo || '').trim();
      if (!tipo) return;
      if (r.es_ingreso) tiposIngresos.push(tipo);
      else tiposGastos.push(tipo);
    });
    return { tiposGastos, tiposIngresos };
  };

  // Formatear números a Moneda Argentina
  const fmt = (num) => "$" + Number(num || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  try {
    const update = req.body;
    if (!update || !update.update_id) {
      return res.status(400).json({ error: 'Payload de Telegram no válido.' });
    }
    // FASE 7: se migra Sheets -> Supabase, pero NO se cambia todavía la
    // estrategia de deduplicación por update_id/last_update_id. El webhook
    // actual valida que exista update_id, pero no lo persiste ni lo usa para
    // descartar retries; esa decisión queda pendiente para una fase posterior.

    // ----- CASO A: Documento PDF recibido -> Derivar a GitHub Actions -----
    const message = update.message;
    if (message && message.document && message.document.mime_type === 'application/pdf') {
      console.log("[INFO] PDF detectado. Derivando a GitHub Actions...");
      await sendTelegram("📄 Factura en PDF recibida. Procesando lectura y guardado en segundo plano...");

      const githubOwner = "dgamero21";
      const githubRepo = "Saldos";
      const githubPat = process.env.GH_PAT;

      const githubUrl = `https://api.github.com/repos/${githubOwner}/${githubRepo}/dispatches`;
      await fetch(githubUrl, {
        method: 'POST',
        headers: {
          'Authorization': `token ${githubPat}`,
          'Accept': 'application/vnd.github.v3+json',
          'Content-Type': 'application/json',
          'User-Agent': 'Vercel-Webhook-Bridge'
        },
        body: JSON.stringify({
          event_type: 'telegram_trigger',
          client_payload: { update }
        })
      });

      return res.status(200).json({ message: 'PDF derivado a GitHub Actions.' });
    }

    // ----- CASO B: Texto (Ingreso de Monto o Consultas Inteligentes) -----
    if (message && message.text) {
      const chat_id = String(message.chat.id);
      
      if (!TELEGRAM_CHAT_ID || chat_id === String(TELEGRAM_CHAT_ID)) {
        const texto = message.text.trim();
        const textoLower = texto.toLowerCase();

        // B.1. Detección de Monto Manual (+17M, 200mil, 17k, 5000)
        const esIngreso = texto.startsWith("+");
        let clean = texto.replace(/\+/g, "").trim().toLowerCase().replace(/\$/g, "").replace(/\s+/g, "");

        let factor = 1.0;
        if (clean.includes("m") || clean.includes("millon") || clean.includes("millones")) {
          factor = 1000000.0;
          clean = clean.replace(/millones|millon|m/g, "");
        } else if (clean.includes("mil") || clean.includes("k")) {
          factor = 1000.0;
          clean = clean.replace(/mil|k/g, "");
        }

        clean = clean.replace(/\./g, "").replace(/,/g, ".");
        const numVal = parseFloat(clean);

        if (!isNaN(numVal) && numVal > 0) {
          const montoFinal = Math.round(numVal * factor * 100) / 100;
          
          try {
            const { tiposGastos, tiposIngresos } = await obtenerCategorias();

            const categorias = esIngreso ? tiposIngresos : tiposGastos;
            const prefix = esIngreso ? "INGRESO" : "MANUAL";
            const titulo = esIngreso ? "💰 ¿A qué categoría de INGRESO corresponde el monto de" : "❓ ¿A qué categoría de GASTO corresponde el monto de";

            const keyboard = [];
            let fila = [];
            categorias.forEach(cat => {
              fila.push({ text: cat, callback_data: `${prefix}|${montoFinal}|${cat}` });
              if (fila.length === 2) {
                keyboard.push(fila);
                fila = [];
              }
            });
            if (fila.length > 0) keyboard.push(fila);

            keyboard.push([{ text: "❌ Cancelar", callback_data: "CANCELAR" }]);

            await sendTelegram(`${titulo} ${fmt(montoFinal)}?`, { inline_keyboard: keyboard });
            return res.status(200).json({ message: 'Teclado enviado en tiempo real.' });

          } catch (errSupabase) {
            console.error("[ERROR buscando categorías en Supabase]:", errSupabase);
            await sendTelegram(`❌ Error al conectar con Supabase desde Vercel: ${errSupabase.message}`);
            return res.status(500).json({ error: errSupabase.message });
          }
        }

        // B.2. CONSULTA: Vencimientos / Facturas Pendientes
        if (textoLower.includes("vencimiento") || textoLower.includes("debo") || textoLower.includes("proximo") || textoLower.includes("factura")) {
          try {
            const rows = await supabaseQuery(
              'consolidado',
              '?select=remitente,asunto,monto_total,fecha_vencimiento&order=id.asc&limit=100'
            );
            
            if (rows.length === 0) {
              await sendTelegram("📋 No hay resúmenes ni facturas registradas en la pestaña Consolidado.");
              return res.status(200).json({ message: 'Consulta sin datos.' });
            }

            let msgs = ["📅 **Próximos Vencimientos y Facturas:**\n"];
            let totalFacturas = 0;

            rows.forEach(r => {
              const emisor = r.remitente || r.asunto || "Factura";
              const monto = parseFloat(r.monto_total) || 0;
              const vto = toArDate(r.fecha_vencimiento) || "Sin fecha";
              if (monto > 0) {
                msgs.push(`• **${emisor}**: ${fmt(monto)} (Vence: ${vto})`);
                totalFacturas += monto;
              }
            });

            msgs.push(`\n🔴 **Total Pendiente:** ${fmt(totalFacturas)}`);
            await sendTelegram(msgs.join("\n"));
            return res.status(200).json({ message: 'Consulta de vencimientos respondida.' });
          } catch (e) {
            await sendTelegram(`❌ Error al consultar vencimientos: ${e.message}`);
            return res.status(500).json({ error: e.message });
          }
        }

        // B.3. CONSULTA: Balance / Resumen Mensual
        if (textoLower.includes("balance") || textoLower.includes("resumen") || textoLower.includes("mes")) {
          try {
            const hoy = new Date();
            const mesActual = hoy.toLocaleDateString("es-AR", { timeZone: "America/Argentina/Cordoba", month: '2-digit', year: 'numeric' });

            const rowsConsumos = await supabaseQuery(
              'consumos',
              '?select=fecha_consumo,pesos&order=id.asc&limit=500'
            );
            const rowsIngresos = await supabaseQuery(
              'ingresos',
              '?select=fecha,monto&order=id.asc&limit=200'
            );

            let totalGastos = 0;
            rowsConsumos.forEach(r => {
              const monto = parseFloat(r.pesos) || 0;
              if (monthYearAr(r.fecha_consumo) === mesActual) totalGastos += monto;
            });

            let totalIngresos = 0;
            rowsIngresos.forEach(r => {
              const monto = parseFloat(r.monto) || 0;
              if (monthYearAr(r.fecha) === mesActual) totalIngresos += monto;
            });

            const neto = totalIngresos - totalGastos;
            const estadoEmoji = neto >= 0 ? "🟢" : "🔴";

            const respuesta = [
              `📊 **Resumen Financiero del Mes (${mesActual}):**\n`,
              `💰 **Ingresos Totales:** ${fmt(totalIngresos)}`,
              `📌 **Gastos Totales:** ${fmt(totalGastos)}`,
              `_______________________`,
              `${estadoEmoji} **Disponible Neto:** ${fmt(neto)}`
            ].join("\n");

            await sendTelegram(respuesta);
            return res.status(200).json({ message: 'Consulta de balance respondida.' });
          } catch (e) {
            await sendTelegram(`❌ Error al consultar balance: ${e.message}`);
            return res.status(500).json({ error: e.message });
          }
        }

        // B.4. CONSULTA: Cuotas Activas
        if (textoLower.includes("cuota") || textoLower.includes("deuda")) {
          try {
            const rowsConsumos = await supabaseQuery(
              'consumos',
              '?select=detalle,cuota_actual,cuota_total,pesos&order=id.asc&limit=500'
            );

            let msgs = ["💳 **Compras Activas en Cuotas:**\n"];
            let cuotasEncontradas = 0;

            rowsConsumos.forEach(r => {
              const detalle = r.detalle || "Compra";
              const cAct = parseInt(r.cuota_actual) || 1;
              const cTot = parseInt(r.cuota_total) || 1;
              const monto = parseFloat(r.pesos) || 0;

              if (cTot > 1 && cAct <= cTot) {
                msgs.push(`• **${detalle}**: Cuota ${cAct} de ${cTot} (${fmt(monto)}/mes)`);
                cuotasEncontradas++;
              }
            });

            if (cuotasEncontradas === 0) {
              await sendTelegram("💳 No tienes compras en cuotas registradas actualmente.");
            } else {
              await sendTelegram(msgs.join("\n"));
            }
            return res.status(200).json({ message: 'Consulta de cuotas respondida.' });
          } catch (e) {
            await sendTelegram(`❌ Error al consultar cuotas: ${e.message}`);
            return res.status(500).json({ error: e.message });
          }
        }

        // B.5. CONSULTA: Filtro por categoría de Gasto (ej: "gasto niñera")
        if (textoLower.startsWith("gasto ") || textoLower.startsWith("gastos ")) {
          try {
            const catBuscada = textoLower.replace(/gastos|gasto/g, "").trim();
            const hoy = new Date();
            const mesActual = hoy.toLocaleDateString("es-AR", { timeZone: "America/Argentina/Cordoba", month: '2-digit', year: 'numeric' });

            const rowsConsumos = await supabaseQuery(
              'consumos',
              '?select=fecha_consumo,detalle,pesos&order=id.asc&limit=500'
            );
            let totalCat = 0;
            let items = 0;

            rowsConsumos.forEach(r => {
              const detalle = (r.detalle || '').toLowerCase();
              const monto = parseFloat(r.pesos) || 0;

              if (monthYearAr(r.fecha_consumo) === mesActual && detalle.includes(catBuscada)) {
                totalCat += monto;
                items++;
              }
            });

            await sendTelegram(`🔍 **Gastos en '${catBuscada.toUpperCase()}' (${mesActual}):**\n• Registros: ${items}\n• Total Acumulado: ${fmt(totalCat)}`);
            return res.status(200).json({ message: 'Consulta por categoría respondida.' });
          } catch (e) {
            await sendTelegram(`❌ Error al consultar categoría: ${e.message}`);
            return res.status(500).json({ error: e.message });
          }
        }
      }
    }

    // ----- CASO C: Selección de Botón (Gasto, Ingreso o Cancelar) -----
    const callbackQuery = update.callback_query;
    if (callbackQuery) {
      const chat_id = String(callbackQuery.message.chat.id);
      if (!TELEGRAM_CHAT_ID || chat_id === String(TELEGRAM_CHAT_ID)) {
        const dataSel = callbackQuery.data;
        const messageId = callbackQuery.message.message_id;

        if (dataSel === "CANCELAR") {
          await editTelegram(messageId, "❌ Operación cancelada.");
          return res.status(200).json({ message: 'Operación cancelada.' });
        }

        const hoy = todayAr();
        const hoyIsoVal = todayIso();

        if (dataSel.startsWith("MANUAL|")) {
          const [, montoStr, catSel] = dataSel.split("|");
          const montoVal = parseFloat(montoStr);

          await supabaseInsert('consumos', {
            fecha_consumo: hoyIsoVal,
            comprobante: 'Telegram',
            detalle: catSel,
            cuota_actual: 1,
            cuota_total: 1,
            pesos: montoVal,
            dolar: 0.0,
            fecha_cierre: null,
            fecha_vencimiento: null,
            remitente: 'Manual Telegram',
            id_consumo: `${hoy}|Telegram|${catSel}|1|Manual Telegram`
          }, {
            onConflict: 'fecha_consumo,comprobante,detalle,cuota_total,remitente'
          });

          await editTelegram(messageId, `✅ ¡Gasto de ${fmt(montoVal)} registrado con éxito en '${catSel}'!`);
          return res.status(200).json({ message: 'Gasto registrado instantáneamente.' });

        } else if (dataSel.startsWith("INGRESO|")) {
          const [, montoStr, catSel] = dataSel.split("|");
          const montoVal = parseFloat(montoStr);

          await supabaseInsert('ingresos', {
            fecha: hoyIsoVal,
            tipo: catSel,
            monto: montoVal,
            origen: 'Manual Telegram',
            id_ingreso: `${hoy}|Ingreso|${catSel}|Manual Telegram`
          }, {
            onConflict: 'fecha,tipo,origen'
          });

          await editTelegram(messageId, `💰 ¡Ingreso de ${fmt(montoVal)} registrado con éxito en '${catSel}'!`);
          return res.status(200).json({ message: 'Ingreso registrado instantáneamente.' });
        }
      }
    }

    return res.status(200).json({ message: 'Petición recibida.' });

  } catch (error) {
    console.error('[ERROR general en Vercel]:', error);
    await sendTelegram(`❌ Error interno en Vercel: ${error.message}`);
    return res.status(500).json({ error: error.message });
  }
}
