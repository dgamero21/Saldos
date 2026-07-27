const https = require('https');

module.exports = async (req, res) => {
  // Solo procesar peticiones POST (que son las que envía Telegram)
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).end('Method Not Allowed');
  }

  // Leer el Personal Access Token de GitHub desde las variables de entorno de Vercel
  const pat = process.env.GH_PAT;
  if (!pat) {
    return res.status(500).send('Error: GH_PAT environment variable is not configured in Vercel.');
  }

  const payload = JSON.stringify({
    event_type: 'telegram_trigger'
  });

  const options = {
    hostname: 'api.github.com',
    port: 443,
    path: '/repos/dgamero21/Saldos/dispatches',
    method: 'POST',
    headers: {
      'Accept': 'application/vnd.github+json',
      'Authorization': `Bearer ${pat}`,
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'Vercel-Serverless-Bridge',
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(payload)
    }
  };

  return new Promise((resolve) => {
    const postReq = https.request(options, (postRes) => {
      let data = '';
      postRes.on('data', (chunk) => {
        data += chunk;
      });

      postRes.on('end', () => {
        if (postRes.statusCode >= 200 && postRes.statusCode < 300) {
          res.status(200).send('OK: GitHub Action triggered successfully.');
          resolve();
        } else {
          res.status(postRes.statusCode).send(`Error from GitHub: ${data}`);
          resolve();
        }
      });
    });

    postReq.on('error', (err) => {
      res.status(500).send(`Network Error: ${err.message}`);
      resolve();
    });

    postReq.write(payload);
    postReq.end();
  });
};
