export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Método no permitido. Utilizar POST.' });
  }

  try {
    const update = req.body;
    if (!update || !update.update_id) {
      return res.status(400).json({ error: 'Estructura de payload no válida.' });
    }

    const TELEGRAM_TOKEN = process.env.TELEGRAM_TOKEN;
    const TELEGRAM_CHAT_ID = process.env.TELEGRAM_CHAT_ID;
    const SHEET_ID = process.env.SHEET_ID;

    // ----- AUXILIAR 1: Enviar mensaje rápido a Telegram -----
    const sendTelegram = async (text, replyMarkup = null) => {
      const url = `https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage`;
      const body = { chat_id: TELEGRAM_CHAT_ID, text };
      if (replyMarkup) body.reply_markup = replyMarkup;
      await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
    };

    // ----- AUXILIAR 2: Editar mensaje de Telegram -----
    const editTelegram = async (messageId, text) => {
      const url = `https://api.telegram.org/bot${TELEGRAM_TOKEN}/editMessageText`;
      await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: TELEGRAM_CHAT_ID, message_id: messageId, text })
      });
    };

    // ----- AUXILIAR 3: Obtener Google Access Token (100ms) -----
    const getGoogleAccessToken = async () => {
      const resToken = await fetch("https://oauth2.googleapis.com/token", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          client_id: process.env.GOOGLE_CLIENT_ID,
          client_secret: process.env.GOOGLE_CLIENT_SECRET,
          refresh_token: process.env.GOOGLE_REFRESH_TOKEN,
          grant_type: "refresh_token"
        })
      });
      const dataToken = await resToken.json();
      return dataToken.access_token;
    };

    // ----- CASO A: Documento PDF recibido -> Derivar a GitHub Actions -----
    const message = update.message;
    if (message && message.document && message.document.mime_type === 'application/pdf') {
      console.log("[INFO] PDF detectado en Telegram. Derivando a GitHub Actions...");
      
      // Notificar al usuario de inmediato
      await sendTelegram("📄 Factura en PDF recibida. Procesando lectura y guardado en segundo plano...");

      // Disparar GitHub Actions
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

      return res.status(200).json({ message: 'PDF derivado exitosamente a GitHub Actions.' });
    }

    // ----- CASO B: Texto de Gasto/Ingreso Manual (ej: "200mil", "+17M") -----
    if (message && message.text) {
      const chat_id = String(message.chat.id);
      if (chat_id === TELEGRAM_CHAT_ID) {
        const texto = message.text.trim();
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
          
          // Obtener categorías directamente desde Google Sheets (Config)
          const accessToken = await getGoogleAccessToken();
          const sheetUrl = `https://sheets.googleapis.com/v4/spreadsheets/${SHEET_ID}/values/Config!E2:J100`;
          const resSheet = await fetch(sheetUrl, { headers: { Authorization: `Bearer ${accessToken}` } });
          const dataSheet = await resSheet.json();
          
          const rows = dataSheet.values || [];
          const tiposGastos = [];
          const tiposIngresos = [];

          rows.forEach(r => {
            if (r[0] && r[0].trim()) tiposGastos.push(r[0].trim());
            if (r[4] && r[4].trim()) tiposIngresos.push(r[4].trim());
          });

          const categorias = esIngreso ? tiposIngresos : tiposGastos;
          const prefix = esIngreso ? "INGRESO" : "MANUAL";
          const titulo = esIngreso ? "💰 ¿A qué categoría de INGRESO corresponde el monto de" : "❓ ¿A qué categoría de GASTO corresponde el monto de";

          // Armar teclado de botones
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

          await sendTelegram(`${titulo} $${montoFinal.toLocaleString('en-US', { minimumFractionDigits: 2 })}?`, { inline_keyboard: keyboard });
          return res.status(200).json({ message: 'Teclado enviado en tiempo real.' });
        }
      }
    }

    // ----- CASO C: Selección de Botón (Gasto o Ingreso) -----
    const callbackQuery = update.callback_query;
    if (callbackQuery) {
      const chat_id = String(callbackQuery.message.chat.id);
      if (chat_id === TELEGRAM_CHAT_ID) {
        const dataSel = callbackQuery.data;
        const messageId = callbackQuery.message.message_id;

        const hoy = new Date().toLocaleDateString("es-AR", { timeZone: "America/Argentina/Cordoba", day: '2-digit', month: '2-digit', year: 'numeric' });

        if (dataSel.startswith ? dataSel.startswith("MANUAL|") : dataSel.indexOf("MANUAL|") === 0) {
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

          await editTelegram(messageId, `✅ ¡Gasto de $${montoVal.toLocaleString('en-US', { minimumFractionDigits: 2 })} registrado con éxito en '${catSel}'!`);
          return res.status(200).json({ message: 'Gasto registrado instantáneamente.' });

        } else if (dataSel.startswith ? dataSel.startswith("INGRESO|") : dataSel.indexOf("INGRESO|") === 0) {
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

          await editTelegram(messageId, `💰 ¡Ingreso de $${montoVal.toLocaleString('en-US', { minimumFractionDigits: 2 })} registrado con éxito en '${catSel}'!`);
          return res.status(200).json({ message: 'Ingreso registrado instantáneamente.' });
        }
      }
    }

    return res.status(200).json({ message: 'Update no requerido procesado.' });

  } catch (error) {
    console.error('[ERROR en Vercel Webhook]:', error);
    return res.status(500).json({ error: 'Fallo interno en el webhook de Vercel.', details: error.message });
  }
}
