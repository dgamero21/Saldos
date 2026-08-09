// FASE 11C — GET / (sirve Index.html con API_APP_TOKEN inyectado)
// Edge function que lee web/Index.html y reemplaza __API_TOKEN__ por el valor de env.

import fs from 'fs';
import path from 'path';

let cachedHtml = null;

export default async function handler(req, res) {
  // Solo GET
  if (req.method !== 'GET') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  try {
    // Cache simple: leer archivo una sola vez
    if (!cachedHtml) {
      const htmlPath = path.join(process.cwd(), 'web', 'Index.html');
      cachedHtml = fs.readFileSync(htmlPath, 'utf-8');
    }

    const token = process.env.API_APP_TOKEN || '';
    const html = cachedHtml.replace('__API_TOKEN__', token);

    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    res.setHeader('Cache-Control', 'public, max-age=300, s-maxage=600');
    return res.status(200).send(html);
  } catch (e) {
    console.error('[index] error:', e);
    return res.status(500).send('Error interno');
  }
}