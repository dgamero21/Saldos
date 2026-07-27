export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Método no permitido. Utilizar POST.' });
  }

  try {
    const update = req.body;

    if (!update || !update.update_id) {
      return res.status(400).json({ error: 'Estructura de payload de Telegram no válida.' });
    }

    const githubOwner = "dgamero21";
    const githubRepo = "Saldos";
    const githubPat = process.env.GH_PAT; // Asegúrese de que esta variable esté configurada en Vercel

    if (!githubPat) {
      console.error("[ERROR] No se detectó la variable GH_PAT en el entorno de Vercel.");
      return res.status(500).json({ error: 'Falta configurar la credencial de GitHub en el servidor.' });
    }

    // Llamada a la API de GitHub para disparar el flujo por evento con carga útil
    const githubUrl = `https://api.github.com/repos/${githubOwner}/${githubRepo}/dispatches`;
    
    const response = await fetch(githubUrl, {
      method: 'POST',
      headers: {
        'Authorization': `token ${githubPat}`,
        'Accept': 'application/vnd.github.v3+json',
        'Content-Type': 'application/json',
        'User-Agent': 'Vercel-Webhook-Bridge'
      },
      body: JSON.stringify({
        event_type: 'telegram_trigger',
        client_payload: {
          update: update // Pasamos todo el objeto recibido de Telegram
        }
      })
    });

    if (response.ok) {
      console.log(`[OK] Evento enviado correctamente a GitHub para el update_id: ${update.update_id}`);
      return res.status(200).json({ message: 'Evento de Telegram propagado a GitHub con éxito.' });
    } else {
      const errorText = await response.text();
      console.error(`[ERROR] GitHub API respondió con estado ${response.status}: ${errorText}`);
      return res.status(response.status).json({ error: 'Error al reportar evento a la API de GitHub.', details: errorText });
    }

  } catch (error) {
    console.error('[ERROR] Ocurrió un fallo en el puente de ejecución:', error);
    return res.status(500).json({ error: 'Fallo interno del servidor en el puente.', details: error.message });
  }
}
