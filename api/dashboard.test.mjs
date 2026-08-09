import test from 'node:test';
import assert from 'node:assert/strict';

// IMPORTANTE: setear env ANTES de importar handler (ESM cachea módulos)
process.env.SUPABASE_URL = 'https://example.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'service-role';
process.env.API_APP_TOKEN = 'test-token-123';

import handler from './dashboard.js';

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => 'application/json' },
    async text() { return JSON.stringify(data); },
    async json() { return data; }
  };
}

function makeFetchImpl(responses) {
  return async (url, options = {}) => {
    for (const [matcher, response] of Object.entries(responses)) {
      if (url.includes(matcher)) {
        return response;
      }
    }
    throw new Error(`fetch inesperado: ${url}`);
  };
}

async function invoke({ fetchImpl, method = 'GET', headers = {} }) {
  const originalFetch = global.fetch;
  global.fetch = fetchImpl;

  const req = { method, headers };
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
  }
}

test('sin token → 401', async () => {
  const result = await invoke({
    fetchImpl: async () => { throw new Error('no debería llamar fetch'); }
  });
  assert.equal(result.statusCode, 401);
  assert.equal(result.body.error, 'Token requerido');
});

test('token inválido → 401', async () => {
  const result = await invoke({
    fetchImpl: async () => { throw new Error('no debería llamar fetch'); },
    headers: { Authorization: 'Bearer wrong-token' }
  });
  assert.equal(result.statusCode, 401);
  assert.equal(result.body.error, 'Token invalido');
});

test('con token válido → 200 y estructura esperada (mocks)', async () => {
  const fetchImpl = async (url, options = {}) => {
    if (url.includes('rest/v1/reglas')) return jsonResponse([
      { remitente: 'bna', entidad: 'BNA' },
      { remitente: 'epec', entidad: 'EPEC' },
      { remitente: 'naranja', entidad: 'NARANJA' },
    ]);
    if (url.includes('rest/v1/consumos')) return jsonResponse([
      { fecha_consumo: '2026-07-15', comprobante: '001', detalle: 'SUPERMERCADO', cuota_actual: 1, cuota_total: 1, pesos: 5000, dolar: 0, fecha_vencimiento: '2026-07-20', remitente: 'bna', pertenece: 'David' },
      { fecha_consumo: '2026-07-10', comprobante: '002', detalle: 'COMBUSTIBLE', cuota_actual: 2, cuota_total: 6, pesos: 10000, dolar: 0, fecha_vencimiento: '2026-08-05', remitente: 'telegram manual', pertenece: 'David' },
    ]);
    if (url.includes('rest/v1/ingresos')) return jsonResponse([
      { fecha: '2026-07-01', tipo: 'SUELDO', monto: 50000, pertenece: 'David' },
    ]);
    if (url.includes('rest/v1/consolidado')) return jsonResponse([
      { remitente: 'bna', asunto: 'Resumen', monto_total: 1000, fecha_vencimiento: '2026-07-25', link_drive: 'https://drive.google.com/file/d/abc', pagado: true, pertenece: 'David' },
      { remitente: 'epec', asunto: 'Factura', monto_total: 2500.5, fecha_vencimiento: '2026-07-30', link_drive: '', pagado: false, pertenece: 'David' },
    ]);
    if (url.includes('rest/v1/reglas')) return jsonResponse([
      { remitente: 'bna', entidad: 'BNA' },
      { remitente: 'epec', entidad: 'EPEC' },
      { remitente: 'naranja', entidad: 'NARANJA' },
    ]);
    throw new Error(`fetch inesperado: ${url}`);
  };

  const result = await invoke({ 
    fetchImpl,
    headers: { Authorization: 'Bearer test-token-123' }
  });

  assert.equal(result.statusCode, 200);
  const data = result.body;

  assert.ok(Array.isArray(data.consumos), 'consumos array');
  assert.ok(Array.isArray(data.ingresos), 'ingresos array');
  assert.ok(Array.isArray(data.vencimientos), 'vencimientos array');
  assert.equal(data.error, null);

  const c = data.consumos[0];
  assert.ok('fecha' in c);
  assert.ok('anio' in c);
  assert.ok('mes' in c);
  assert.ok('detalle' in c);
  assert.ok('cuotaActual' in c);
  assert.ok('cuotaTotal' in c);
  assert.ok('monto' in c);
  assert.ok('entidad' in c);
  assert.ok('pertenece' in c);

  const i = data.ingresos[0];
  assert.ok('fecha' in i);
  assert.ok('anio' in i);
  assert.ok('mes' in i);
  assert.ok('tipo' in i);
  assert.ok('monto' in i);
  assert.ok('pertenece' in i);

  const v = data.vencimientos.find(v => v.entidad === 'BNA');
  assert.ok(v, 'vencimiento BNA presente');
  assert.equal(v.estado, 'PAGADO');
  assert.equal(v.entidad, 'BNA');
  assert.ok(v.icono);

  const manual = data.vencimientos.find(v => v.hojaOrigen === 'consumos');
  assert.ok(manual, 'vencimiento manual presente');
  assert.equal(manual.estado, 'PENDIENTE');
  assert.equal(manual.hojaOrigen, 'consumos');
  assert.equal(manual.icono, 'payments');
});

test('error de Supabase devuelve error en payload (no 500)', async () => {
  const fetchImpl = async (url) => {
    if (url.includes('rest/v1/reglas')) return jsonResponse([]);
    if (url.includes('rest/v1/consumos')) return jsonResponse({ error: 'boom' }, 500);
    throw new Error(`fetch inesperado: ${url}`);
  };

  const result = await invoke({ 
    fetchImpl,
    headers: { Authorization: 'Bearer test-token-123' }
  });

  assert.equal(result.statusCode, 200);
  const data = result.body;
  assert.ok(typeof data.error === 'string');
  assert.ok(data.error.includes('boom') || data.error.includes('Supabase'));
  assert.deepEqual(data.consumos, []);
  assert.deepEqual(data.ingresos, []);
  assert.deepEqual(data.vencimientos, []);
});