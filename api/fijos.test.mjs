import test from 'node:test';
import assert from 'node:assert/strict';

// IMPORTANTE: setear env ANTES de importar handlers
process.env.SUPABASE_URL = 'https://example.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'service-role';
process.env.API_APP_TOKEN = 'test-token-123';

import handlerPago from './pago.js';
import handlerFijos from './fijos.js';
import handlerIngreso from './ingreso.js';

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => 'application/json' },
    async text() { return JSON.stringify(data); },
    async json() { return data; }
  };
}

async function invoke({ handler, fetchImpl, method = 'POST', headers = {}, body }) {
  const originalFetch = global.fetch;
  global.fetch = fetchImpl;

  const req = { method, headers, body };
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

// --- Tests /api/pago ---
test('pago: sin token → 401', async () => {
  const result = await invoke({ handler: handlerPago, fetchImpl: async () => { throw new Error('no fetch'); } });
  assert.equal(result.statusCode, 401);
});

test('pago: token inválido → 401', async () => {
  const result = await invoke({ handler: handlerPago, fetchImpl: async () => { throw new Error('no fetch'); }, headers: { Authorization: 'Bearer wrong' } });
  assert.equal(result.statusCode, 401);
});

test('pago: faltan campos → 400', async () => {
  const result = await invoke({ handler: handlerPago, headers: { Authorization: 'Bearer test-token-123' }, body: {} });
  assert.equal(result.statusCode, 400);
});

test('pago: estado inválido → 400', async () => {
  const result = await invoke({
    handler: handlerPago,
    headers: { Authorization: 'Bearer test-token-123' },
    body: { hojaOrigen: 'Consolidado', rowId: 1, estado: 'INVALIDO' }
  });
  assert.equal(result.statusCode, 400);
});

test('pago: hojaOrigen inválido → 400', async () => {
  const result = await invoke({
    handler: handlerPago,
    headers: { Authorization: 'Bearer test-token-123' },
    body: { hojaOrigen: 'Invalido', rowId: 1, estado: 'PAGADO' }
  });
  assert.equal(result.statusCode, 400);
});

test('pago: PAGADO en Consolidado → 200 (mock)', async () => {
  const fetchImpl = async (url) => {
    if (url.includes('rest/v1/consolidado')) return jsonResponse([{ id: 1, pagado: true }]);
    throw new Error(`fetch inesperado: ${url}`);
  };
  const result = await invoke({
    handler: handlerPago,
    fetchImpl,
    headers: { Authorization: 'Bearer test-token-123' },
    body: { hojaOrigen: 'Consolidado', rowId: 1, estado: 'PAGADO' }
  });
  assert.equal(result.statusCode, 200);
  assert.equal(result.body.success, true);
  assert.equal(result.body.estado, 'PAGADO');
});

test('pago: PENDIENTE en consumos → 200 (mock)', async () => {
  const fetchImpl = async (url) => {
    if (url.includes('rest/v1/consumos')) return jsonResponse([{ id: 1, dolar: null }]);
    throw new Error(`fetch inesperado: ${url}`);
  };
  const result = await invoke({
    handler: handlerPago,
    fetchImpl,
    headers: { Authorization: 'Bearer test-token-123' },
    body: { hojaOrigen: 'consumos', rowId: 1, estado: 'PENDIENTE' }
  });
  assert.equal(result.statusCode, 200);
  assert.equal(result.body.estado, 'PENDIENTE');
});

// --- Tests /api/fijos ---
test('fijos: sin token → 401', async () => {
  const result = await invoke({ handler: handlerFijos, fetchImpl: async () => { throw new Error('no fetch'); } });
  assert.equal(result.statusCode, 401);
});

test('fijos: listar → 200 (mock)', async () => {
  const fetchImpl = async (url) => {
    if (url.includes('rest/v1/categorias_fijas')) return jsonResponse([
      { id: 1, tipo: 'ALQUILER', monto_fijo: 10000, es_ingreso: false, activo: true, pertenece: 'David' },
      { id: 2, tipo: 'SUELDO', monto_fijo: 50000, es_ingreso: true, activo: true, pertenece: 'David' }
    ]);
    throw new Error(`fetch inesperado: ${url}`);
  };
  const result = await invoke({
    handler: handlerFijos,
    fetchImpl,
    headers: { Authorization: 'Bearer test-token-123' },
    body: { accion: 'listar' }
  });
  assert.equal(result.statusCode, 200);
  assert.ok(Array.isArray(result.body.data));
  assert.equal(result.body.data.length, 2);
  assert.equal(result.body.data[0].tipo, 'ALQUILER');
  assert.equal(result.body.data[1].es_ingreso, true);
});

test('fijos: crear gasto → 201 (mock)', async () => {
  const fetchImpl = async (url, options) => {
    if (url.includes('rest/v1/categorias_fijas') && options?.method === 'POST') {
      return jsonResponse([{ id: 3, tipo: 'LUZ', monto_fijo: 5000, es_ingreso: false, activo: true, pertenece: 'David' }], 201);
    }
    throw new Error(`fetch inesperado: ${url}`);
  };
  const result = await invoke({
    handler: handlerFijos,
    fetchImpl,
    headers: { Authorization: 'Bearer test-token-123' },
    body: { accion: 'crear', tipo: 'LUZ', monto_fijo: 5000, es_ingreso: false }
  });
  assert.equal(result.statusCode, 201);
  assert.equal(result.body.success, true);
});

test('fijos: crear ingreso → 201 (mock)', async () => {
  const fetchImpl = async (url, options) => {
    if (url.includes('rest/v1/categorias_fijas') && options?.method === 'POST') {
      return jsonResponse([{ id: 4, tipo: 'BONO', monto_fijo: 10000, es_ingreso: true, activo: true, pertenece: 'David' }], 201);
    }
    throw new Error(`fetch inesperado: ${url}`);
  };
  const result = await invoke({
    handler: handlerFijos,
    fetchImpl,
    headers: { Authorization: 'Bearer test-token-123' },
    body: { accion: 'crear', tipo: 'BONO', monto_fijo: 10000, es_ingreso: true }
  });
  assert.equal(result.statusCode, 201);
  assert.equal(result.body.data.es_ingreso, true);
});

test('fijos: actualizar → 200 (mock)', async () => {
  const fetchImpl = async (url, options) => {
    if (url.includes('rest/v1/categorias_fijas') && options?.method === 'PATCH') {
      return jsonResponse([{ id: 1, tipo: 'ALQUILER ACTUALIZADO', monto_fijo: 12000, activo: true }]);
    }
    throw new Error(`fetch inesperado: ${url}`);
  };
  const result = await invoke({
    handler: handlerFijos,
    fetchImpl,
    headers: { Authorization: 'Bearer test-token-123' },
    body: { accion: 'actualizar', id: 1, tipo: 'ALQUILER ACTUALIZADO', monto_fijo: 12000, activo: true }
  });
  assert.equal(result.statusCode, 200);
  assert.equal(result.body.success, true);
});

test('fijos: eliminar (soft delete) → 200 (mock)', async () => {
  const fetchImpl = async (url, options) => {
    if (url.includes('rest/v1/categorias_fijas') && options?.method === 'PATCH') {
      return jsonResponse([{ id: 1, activo: false }]);
    }
    throw new Error(`fetch inesperado: ${url}`);
  };
  const result = await invoke({
    handler: handlerFijos,
    fetchImpl,
    headers: { Authorization: 'Bearer test-token-123' },
    body: { accion: 'eliminar', id: 1 }
  });
  assert.equal(result.statusCode, 200);
  assert.equal(result.body.success, true);
});

// --- Tests /api/ingreso ---
test('ingreso: sin token → 401', async () => {
  const result = await invoke({ handler: handlerIngreso, fetchImpl: async () => { throw new Error('no fetch'); } });
  assert.equal(result.statusCode, 401);
});

test('ingreso: faltan campos → 400', async () => {
  const result = await invoke({ handler: handlerIngreso, headers: { Authorization: 'Bearer test-token-123' }, body: {} });
  assert.equal(result.statusCode, 400);
});

test('ingreso: monto inválido → 400', async () => {
  const result = await invoke({
    handler: handlerIngreso,
    headers: { Authorization: 'Bearer test-token-123' },
    body: { tipo: 'SUELDO', monto: -100 }
  });
  assert.equal(result.statusCode, 400);
});

test('ingreso: categoría no existe → 400 (mock)', async () => {
  const fetchImpl = async (url) => {
    if (url.includes('rest/v1/categorias_fijas')) return jsonResponse([]);
    throw new Error(`fetch inesperado: ${url}`);
  };
  const result = await invoke({
    handler: handlerIngreso,
    fetchImpl,
    headers: { Authorization: 'Bearer test-token-123' },
    body: { tipo: 'INEXISTENTE', monto: 1000 }
  });
  assert.equal(result.statusCode, 400);
});

test('ingreso: éxito → 201 (mock)', async () => {
  const fetchImpl = async (url, options) => {
    if (url.includes('rest/v1/categorias_fijas')) return jsonResponse([{ id: 1, tipo: 'SUELDO', es_ingreso: true, activo: true }]);
    if (url.includes('rest/v1/ingresos') && options?.method === 'POST') {
      return jsonResponse([{ id: 5, tipo: 'SUELDO', monto: 1000, origen: 'Manual WebApp' }], 201);
    }
    throw new Error(`fetch inesperado: ${url}`);
  };
  const result = await invoke({
    handler: handlerIngreso,
    fetchImpl,
    headers: { Authorization: 'Bearer test-token-123' },
    body: { tipo: 'SUELDO', monto: 1000 }
  });
  assert.equal(result.statusCode, 201);
  assert.equal(result.body.success, true);
});

test('ingreso: error Supabase → 500', async () => {
  const fetchImpl = async (url) => {
    if (url.includes('rest/v1/categorias_fijas')) return jsonResponse([{ id: 1, tipo: 'SUELDO', es_ingreso: true, activo: true }]);
    if (url.includes('rest/v1/ingresos')) return jsonResponse({ error: 'boom' }, 500);
    throw new Error(`fetch inesperado: ${url}`);
  };
  const result = await invoke({
    handler: handlerIngreso,
    fetchImpl,
    headers: { Authorization: 'Bearer test-token-123' },
    body: { tipo: 'SUELDO', monto: 1000 }
  });
  assert.equal(result.statusCode, 500);
});