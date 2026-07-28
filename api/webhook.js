export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Método no permitido. Utilizar POST.' });
  }

  const TELEGRAM_TOKEN = process.env.TELEGRAM_TOKEN;
  const TELEGRAM_CHAT_ID = process.env.TELEGRAM_CHAT_ID;
  const SHEET_ID = process.env.SHEET_ID;

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

  // Auxiliar para obtener Google Access Token
  const getGoogleAccessToken = async () => {
    const resToken = await fetch("https://oauth2.googleapis.com/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        client_id: process.env.GOOGLE_CLIENT_ID || "",
        client_secret: process.env.GOOGLE_CLIENT_SECRET || "",
        refresh_token: process.env.GOOGLE_REFRESH_TOKEN || "",
        grant_type: "refresh_token"
      })
    });
    const dataToken = await resToken.json();
    if (!dataToken.access_token) {
      throw new Error(`No se pudo obtener token de Google: ${JSON.stringify(dataToken)}`);
    }
    return dataToken.access_token;
  };

  // Auxiliar para consultar datos de cualquier pestaña de Google Sheets
  const fetchSheetValues = async (accessToken, range) => {
    const url = `https://sheets.googleapis.com/v4/spreadsheets/${SHEET_ID}/values/${encodeURIComponent(range)}`;
    const res = await fetch(url, { headers: { Authorization: `Bearer ${accessToken}` } });
    const data = await res.json();
    return data.values || [];
  };

  // Formatear números a Moneda Argentina
  const fmt = (num) => "$" + Number(num || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  try {
    const update = req.body;
    if (!update || !update.update_id) {
      return res.status(400).json({ error: 'Payload de Telegram no válido.' });
    }

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
            const accessToken = await getGoogleAccessToken();
            const rows = await fetchSheetValues(accessToken, "Config!E2:J100");
            
            const tiposGastos = [];
            const tiposIngresos = [];

            rows.forEach(r => {
              if (r[0] && r[0].trim()) tiposGastos.push(r[0].trim());
              if (r[4] && r[4].trim()) tiposIngresos.push(r[4].trim());
            });

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

          } catch (errSheet) {
            console.error("[ERROR buscando en Sheets]:", errSheet);
            await sendTelegram(`❌ Error al conectar con Google Sheets desde Vercel: ${errSheet.message}`);
            return res.status(500).json({ error: errSheet.message });
          }
        }

        // B.2. CONSULTA: Vencimientos / Facturas Pendientes
        if (textoLower.includes("vencimiento") || textoLower.includes("debo") || textoLower.includes("proximo") || textoLower.includes("factura")) {
          try {
            const accessToken = await getGoogleAccessToken();
            const rows = await fetchSheetValues(accessToken, "Consolidado!A2:F100");
            
            if (rows.length === 0) {
              await sendTelegram("📋 No hay resúmenes ni facturas registradas en la pestaña Consolidado.");
              return res.status(200).json({ message: 'Consulta sin datos.' });
            }

            let msgs = ["📅 **Próximos Vencimientos y Facturas:**\n"];
            let totalFacturas = 0;

            rows.forEach(r => {
              const emisor = r[1] || r[2] || "Factura";
              const monto = parseFloat(r[3]) || 0;
              const vto = r[4] || "Sin fecha";
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
            const accessToken = await getGoogleAccessToken();
            const hoy = new Date();
            const mesActual = hoy.toLocaleDateString("es-AR", { timeZone: "America/Argentina/Cordoba", month: '2-digit', year: 'numeric' });

            const rowsConsumos = await fetchSheetValues(accessToken, "Consumos!A2:K500");
            const rowsIngresos = await fetchSheetValues(accessToken, "Ingresos!A2:E200");

            let totalGastos = 0;
            rowsConsumos.forEach(r => {
              const fecha = r[0] || "";
              const monto = parseFloat(r[5]) || 0;
              if (fecha.includes(mesActual)) totalGastos += monto;
            });

            let totalIngresos = 0;
            rowsIngresos.forEach(r => {
              const fecha = r[0] || "";
              const monto = parseFloat(r[2]) || 0;
              if (fecha.includes(mesActual)) totalIngresos += monto;
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
            const accessToken = await getGoogleAccessToken();
            const rowsConsumos = await fetchSheetValues(accessToken, "Consumos!A2:K500");

            let msgs = ["💳 **Compras Activas en Cuotas:**\n"];
            let cuotasEncontradas = 0;

            rowsConsumos.forEach(r => {
              const detalle = r[2] || "Compra";
              const cAct = parseInt(r[3]) || 1;
              const cTot = parseInt(r[4]) || 1;
              const monto = parseFloat(r[5]) || 0;

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
            const accessToken = await getGoogleAccessToken();
            const hoy = new Date();
            const mesActual = hoy.toLocaleDateString("es-AR", { timeZone: "America/Argentina/Cordoba", month: '2-digit', year: 'numeric' });

            const rowsConsumos = await fetchSheetValues(accessToken, "Consumos!A2:K500");
            let totalCat = 0;
            let items = 0;

            rowsConsumos.forEach(r => {
              const fecha = r[0] || "";
              const detalle = (r[2] || "").toLowerCase();
              const monto = parseFloat(r[5]) || 0;

              if (fecha.includes(mesActual) && detalle.includes(catBuscada)) {
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

        const hoy = new Date().toLocaleDateString("es-AR", { timeZone: "America/Argentina/Cordoba", day: '2-digit', month: '2-digit', year: 'numeric' });

        if (dataSel.startsWith("MANUAL|")) {
          const [, montoStr, catSel] = dataSel.split("|");
          const montoVal = parseFloat(montoStr);

          const accessToken = await getGoogleAccessToken();
          const appendUrl = `https://sheets.googleapis.com/v4/spreadsheets/${SHEET_ID}/values/Consumos!A1:append?valueInputOption=USER_ENTERED`;
          
          await fetch(appendUrl, {
            method: 'POST',
            headers: { Authorization: `Bearer ${accessToken}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({
              values: [[
                hoy, "Telegram", catSel, 1, 1, montoVal, 0.0, "", "", "Manual Telegram", `${hoy}|Telegram|${catSel}|1|Manual Telegram`
              ]]
            })
          });

          await editTelegram(messageId, `✅ ¡Gasto de ${fmt(montoVal)} registrado con éxito en '${catSel}'!`);
          return res.status(200).json({ message: 'Gasto registrado instantáneamente.' });

        } else if (dataSel.startsWith("INGRESO|")) {
          const [, montoStr, catSel] = dataSel.split("|");
          const montoVal = parseFloat(montoStr);

          const accessToken = await getGoogleAccessToken();
          const appendUrl = `https://sheets.googleapis.com/v4/spreadsheets/${SHEET_ID}/values/Ingresos!A1:append?valueInputOption=USER_ENTERED`;
          
          await fetch(appendUrl, {
            method: 'POST',
            headers: { Authorization: `Bearer ${accessToken}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({
              values: [[
                hoy, catSel, montoVal, "Manual Telegram", `${hoy}|Ingreso|${catSel}|Manual Telegram`
              ]]
            })
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
