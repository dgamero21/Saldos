import test from 'node:test';
import assert from 'node:assert/strict';

import handler from './webhook.js';

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => 'application/json' },
    async text() { return JSON.stringify(data); },
    async json() { return data; }
  };
}

function textResponse(text, status = 200, contentType = 'text/plain') {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => contentType },
    async text() { return text; },
    async json() { return JSON.parse(text); }
  };
}

async function invoke({ body, env = {}, fetchImpl, method = 'POST' }) {
  const prevEnv = {
    TELEGRAM_TOKEN: process.env.TELEGRAM_TOKEN,
    TELEGRAM_CHAT_ID: process.env.TELEGRAM_CHAT_ID,
    SUPABASE_URL: process.env.SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY: process.env.SUPABASE_SERVICE_ROLE_KEY,
    GH_PAT: process.env.GH_PAT,
    SHEET_ID: process.env.SHEET_ID,
    GOOGLE_CLIENT_ID: process.env.GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET: process.env.GOOGLE_CLIENT_SECRET,
    GOOGLE_REFRESH_TOKEN: process.env.GOOGLE_REFRESH_TOKEN,
  };
  Object.assign(process.env, {
    TELEGRAM_TOKEN: 'tg-token',
    TELEGRAM_CHAT_ID: 'chat-1',
    SUPABASE_URL: 'https://example.supabase.co',
    SUPABASE_SERVICE_ROLE_KEY: 'service-role',
    GH_PAT: 'gh-pat',
    SHEET_ID: 'sheet-id',
    GOOGLE_CLIENT_ID: 'google-client',
    GOOGLE_CLIENT_SECRET: 'google-secret',
    GOOGLE_REFRESH_TOKEN: 'google-refresh',
    ...env,
  });

  const originalFetch = global.fetch;
  global.fetch = fetchImpl;

  const req = { method, body };
  const result = { statusCode: 200, body: undefined };
  const res = {
    status(code) {
      result.statusCode = code;
      return this;
    },
    json(payload) {
      result.body = payload;
      return this;
    }
  };

  try {
    await handler(req, res);
    return result;
  } finally {
    global.fetch = originalFetch;
    for (const [k, v] of Object.entries(prevEnv)) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
  }
}

test('categorias: monto manual envia teclado con categorias y cancelar', async () => {
  const calls = [];
  const result = await invoke({
    body: { update_id: 1, message: { chat: { id: 'chat-1' }, text: '200k' } },
    fetchImpl: async (url, options = {}) => {
      calls.push({ url, options });
      if (url.includes('/rest/v1/categorias_fijas')) {
        return jsonResponse([
          { es_ingreso: false, tipo: 'ALQUILER' },
          { es_ingreso: false, tipo: 'NIÑERA' },
          { es_ingreso: true, tipo: 'SUELDO' }
        ]);
      }
      if (url.includes('/sendMessage')) {
        return jsonResponse({ ok: true });
      }
      throw new Error(`fetch inesperado: ${url}`);
    }
  });

  assert.equal(result.statusCode, 200);
  const send = calls.find((c) => c.url.includes('/sendMessage'));
  const payload = JSON.parse(send.options.body);
  assert.equal(payload.text, '❓ ¿A qué categoría de GASTO corresponde el monto de $200,000.00?');
  assert.deepEqual(payload.reply_markup.inline_keyboard.at(-1), [{ text: '❌ Cancelar', callback_data: 'CANCELAR' }]);
});

test('categorias: consulta filtra activas (activo=eq.true)', async () => {
  const calls = [];
  const result = await invoke({
    body: { update_id: 3, message: { chat: { id: 'chat-1' }, text: '150k' } },
    fetchImpl: async (url, options = {}) => {
      calls.push({ url, options });
      if (url.includes('/rest/v1/categorias_fijas')) {
        return jsonResponse([
          { es_ingreso: false, tipo: 'ALQUILER' },
          { es_ingreso: false, tipo: 'NIÑERA' },
          { es_ingreso: true, tipo: 'SUELDO' }
        ]);
      }
      if (url.includes('/sendMessage')) {
        return jsonResponse({ ok: true });
      }
      throw new Error(`fetch inesperado: ${url}`);
    }
  });

  assert.equal(result.statusCode, 200);
  const catCall = calls.find((c) => c.url.includes('/rest/v1/categorias_fijas'));
  assert.ok(catCall.url.includes('activo=eq.true'), `URL sin filtro activo: ${catCall.url}`);
});

test('consulta vencimientos usa consolidado y mantiene mensaje', async () => {
  const calls = [];
  await invoke({
    body: { update_id: 2, message: { chat: { id: 'chat-1' }, text: 'vencimiento' } },
    fetchImpl: async (url, options = {}) => {
      calls.push({ url, options });
      if (url.includes('/rest/v1/consolidado')) {
        return jsonResponse([
          { remitente: 'BNA', asunto: 'Resumen', monto_total: 1000, fecha_vencimiento: '2026-08-21' },
          { remitente: 'EPEC', asunto: 'Factura', monto_total: 2500.5, fecha_vencimiento: '2026-08-25' }
        ]);
      }
      if (url.includes('/sendMessage')) return jsonResponse({ ok: true });
      throw new Error(`fetch inesperado: ${url}`);
    }
  });
  const payload = JSON.parse(calls.find((c) => c.url.includes('/sendMessage')).options.body);
  assert.match(payload.text, /📅 \*\*Próximos Vencimientos y Facturas:\*\*/);
  assert.match(payload.text, /BNA/);
  assert.match(payload.text, /21\/08\/2026/);
  assert.match(payload.text, /🔴 \*\*Total Pendiente:\*\* \$3,500\.50/);
});

test('consulta balance usa consumos e ingresos del mes actual', async () => {
  const calls = [];
  const thisMonth = new Date().toLocaleDateString('es-AR', { timeZone: 'America/Argentina/Cordoba', month: '2-digit', year: 'numeric' });
  const [mm, yyyy] = thisMonth.split('/');
  const mmIso = String(mm).padStart(2, '0');
  await invoke({
    body: { update_id: 3, message: { chat: { id: 'chat-1' }, text: 'balance mes' } },
    fetchImpl: async (url, options = {}) => {
      calls.push({ url, options });
      if (url.includes('/rest/v1/consumos')) {
        return jsonResponse([
          { fecha_consumo: `${yyyy}-${mmIso}-05`, pesos: 1000 },
          { fecha_consumo: `1999-01-01`, pesos: 5000 }
        ]);
      }
      if (url.includes('/rest/v1/ingresos')) {
        return jsonResponse([
          { fecha: `${yyyy}-${mmIso}-03`, monto: 7000 },
          { fecha: `1999-01-01`, monto: 1 }
        ]);
      }
      if (url.includes('/sendMessage')) return jsonResponse({ ok: true });
      throw new Error(`fetch inesperado: ${url}`);
    }
  });
  const payload = JSON.parse(calls.find((c) => c.url.includes('/sendMessage')).options.body);
  assert.match(payload.text, /💰 \*\*Ingresos Totales:\*\* \$7,000\.00/);
  assert.match(payload.text, /📌 \*\*Gastos Totales:\*\* \$1,000\.00/);
  assert.match(payload.text, /🟢 \*\*Disponible Neto:\*\* \$6,000\.00/);
});

test('consulta cuotas mantiene formato anterior', async () => {
  const calls = [];
  await invoke({
    body: { update_id: 4, message: { chat: { id: 'chat-1' }, text: 'cuotas' } },
    fetchImpl: async (url, options = {}) => {
      calls.push({ url, options });
      if (url.includes('/rest/v1/consumos')) {
        return jsonResponse([
          { detalle: 'Laptop', cuota_actual: 2, cuota_total: 6, pesos: 1234.56 },
          { detalle: 'Pago contado', cuota_actual: 1, cuota_total: 1, pesos: 50 }
        ]);
      }
      if (url.includes('/sendMessage')) return jsonResponse({ ok: true });
      throw new Error(`fetch inesperado: ${url}`);
    }
  });
  const payload = JSON.parse(calls.find((c) => c.url.includes('/sendMessage')).options.body);
  assert.match(payload.text, /💳 \*\*Compras Activas en Cuotas:\*\*/);
  assert.match(payload.text, /Laptop/);
  assert.match(payload.text, /Cuota 2 de 6/);
});

test('consulta gasto por categoria usa detalle y mes actual', async () => {
  const calls = [];
  const thisMonth = new Date().toLocaleDateString('es-AR', { timeZone: 'America/Argentina/Cordoba', month: '2-digit', year: 'numeric' });
  const [mm, yyyy] = thisMonth.split('/');
  const mmIso = String(mm).padStart(2, '0');
  await invoke({
    body: { update_id: 5, message: { chat: { id: 'chat-1' }, text: 'gasto niñera' } },
    fetchImpl: async (url, options = {}) => {
      calls.push({ url, options });
      if (url.includes('/rest/v1/consumos')) {
        return jsonResponse([
          { fecha_consumo: `${yyyy}-${mmIso}-05`, detalle: 'NIÑERA AGOSTO', pesos: 3000 },
          { fecha_consumo: `${yyyy}-${mmIso}-06`, detalle: 'OTRO', pesos: 999 },
          { fecha_consumo: `1999-01-01`, detalle: 'NIÑERA VIEJO', pesos: 1000 }
        ]);
      }
      if (url.includes('/sendMessage')) return jsonResponse({ ok: true });
      throw new Error(`fetch inesperado: ${url}`);
    }
  });
  const payload = JSON.parse(calls.find((c) => c.url.includes('/sendMessage')).options.body);
  assert.match(payload.text, /Gastos en 'NIÑERA'/);
  assert.match(payload.text, /Registros: 1/);
  assert.match(payload.text, /Total Acumulado: \$3,000\.00/);
});

test('callback MANUAL escribe en consumos y edita mensaje', async () => {
  const calls = [];
  const result = await invoke({
    body: { update_id: 6, callback_query: { data: 'MANUAL|1000|ALQUILER', message: { chat: { id: 'chat-1' }, message_id: 77 } } },
    fetchImpl: async (url, options = {}) => {
      calls.push({ url, options });
      if (url.includes('/rest/v1/consumos')) return textResponse('', 201, 'application/json');
      if (url.includes('/editMessageText')) return jsonResponse({ ok: true });
      throw new Error(`fetch inesperado: ${url}`);
    }
  });
  assert.equal(result.statusCode, 200);
  const insert = calls.find((c) => c.url.includes('/rest/v1/consumos'));
  const row = JSON.parse(insert.options.body)[0];
  assert.equal(row.comprobante, 'Telegram');
  assert.equal(row.detalle, 'ALQUILER');
  assert.equal(row.remitente, 'Manual Telegram');
  const edit = JSON.parse(calls.find((c) => c.url.includes('/editMessageText')).options.body);
  assert.equal(edit.text, "✅ ¡Gasto de $1,000.00 registrado con éxito en 'ALQUILER'!");
});

test('callback INGRESO escribe en ingresos y edita mensaje', async () => {
  const calls = [];
  await invoke({
    body: { update_id: 7, callback_query: { data: 'INGRESO|500|SUELDO', message: { chat: { id: 'chat-1' }, message_id: 88 } } },
    fetchImpl: async (url, options = {}) => {
      calls.push({ url, options });
      if (url.includes('/rest/v1/ingresos')) return textResponse('', 201, 'application/json');
      if (url.includes('/editMessageText')) return jsonResponse({ ok: true });
      throw new Error(`fetch inesperado: ${url}`);
    }
  });
  const row = JSON.parse(calls.find((c) => c.url.includes('/rest/v1/ingresos')).options.body)[0];
  assert.equal(row.tipo, 'SUELDO');
  assert.equal(row.origen, 'Manual Telegram');
  const edit = JSON.parse(calls.find((c) => c.url.includes('/editMessageText')).options.body);
  assert.equal(edit.text, "💰 ¡Ingreso de $500.00 registrado con éxito en 'SUELDO'!");
});

test('callback CANCELAR mantiene mensaje actual', async () => {
  const calls = [];
  await invoke({
    body: { update_id: 8, callback_query: { data: 'CANCELAR', message: { chat: { id: 'chat-1' }, message_id: 90 } } },
    fetchImpl: async (url, options = {}) => {
      calls.push({ url, options });
      if (url.includes('/editMessageText')) return jsonResponse({ ok: true });
      throw new Error(`fetch inesperado: ${url}`);
    }
  });
  const edit = JSON.parse(calls[0].options.body);
  assert.equal(edit.text, '❌ Operación cancelada.');
});

test('TELEGRAM_CHAT_ID bloquea chats ajenos', async () => {
  const calls = [];
  const result = await invoke({
    body: { update_id: 9, message: { chat: { id: 'otro-chat' }, text: 'balance' } },
    fetchImpl: async (url, options = {}) => {
      calls.push({ url, options });
      throw new Error(`no debería llamar fetch: ${url}`);
    }
  });
  assert.equal(result.statusCode, 200);
  assert.deepEqual(result.body, { message: 'Petición recibida.' });
  assert.equal(calls.length, 0);
});

test('error de Supabase informa por Telegram', async () => {
  const calls = [];
  const result = await invoke({
    body: { update_id: 10, message: { chat: { id: 'chat-1' }, text: 'vencimientos' } },
    fetchImpl: async (url, options = {}) => {
      calls.push({ url, options });
      if (url.includes('/rest/v1/consolidado')) return jsonResponse({ error: 'boom' }, 500);
      if (url.includes('/sendMessage')) return jsonResponse({ ok: true });
      throw new Error(`fetch inesperado: ${url}`);
    }
  });
  assert.equal(result.statusCode, 500);
  const send = JSON.parse(calls.find((c) => c.url.includes('/sendMessage')).options.body);
  assert.match(send.text, /❌ Error al consultar vencimientos:/);
});

test('PDF dispatch mantiene repository_dispatch y ack de Telegram', async () => {
  const calls = [];
  const update = { update_id: 11, message: { chat: { id: 'chat-1' }, document: { mime_type: 'application/pdf' } } };
  const result = await invoke({
    body: update,
    fetchImpl: async (url, options = {}) => {
      calls.push({ url, options });
      if (url.includes('/sendMessage')) return jsonResponse({ ok: true });
      if (url.includes('api.github.com/repos/dgamero21/Saldos/dispatches')) return textResponse('', 204, 'application/json');
      throw new Error(`fetch inesperado: ${url}`);
    }
  });
  assert.equal(result.statusCode, 200);
  const send = JSON.parse(calls.find((c) => c.url.includes('/sendMessage')).options.body);
  assert.equal(send.text, '📄 Factura en PDF recibida. Procesando lectura y guardado en segundo plano...');
  const dispatch = JSON.parse(calls.find((c) => c.url.includes('/dispatches')).options.body);
  assert.equal(dispatch.event_type, 'telegram_trigger');
  assert.deepEqual(dispatch.client_payload, { update });
});

test('payload inválido retorna 400', async () => {
  const result = await invoke({
    body: { message: { text: 'hola' } },
    fetchImpl: async () => { throw new Error('no debería llamar fetch'); }
  });
  assert.equal(result.statusCode, 400);
  assert.deepEqual(result.body, { error: 'Payload de Telegram no válido.' });
});
