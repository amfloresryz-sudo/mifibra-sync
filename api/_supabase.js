// Cliente mínimo de la API REST de Supabase. Solo se usa en el servidor (funciones de Vercel):
// la service_role key vive en las variables de entorno de Vercel y nunca llega al navegador.

const URL_BASE = (process.env.SUPABASE_URL || '').replace(/\/$/, '');
const CLAVE = process.env.SUPABASE_SERVICE_KEY || '';

async function consultar(ruta, { contar = false } = {}) {
  if (!URL_BASE || !CLAVE) throw new Error('Faltan SUPABASE_URL o SUPABASE_SERVICE_KEY en Vercel');
  const headers = { apikey: CLAVE, Authorization: `Bearer ${CLAVE}` };
  if (contar) headers.Prefer = 'count=exact';
  const r = await fetch(`${URL_BASE}/rest/v1/${ruta}`, { headers });
  if (!r.ok) throw new Error(`Supabase ${r.status}: ${(await r.text()).slice(0, 300)}`);
  const datos = await r.json();
  const rango = r.headers.get('content-range'); // ej. "0-49/1752"
  const total = rango && rango.includes('/') ? Number(rango.split('/')[1]) : undefined;
  return { datos, total };
}

// Inserta o reemplaza filas (upsert) en una tabla. Solo lo usan los ajustes del portal.
async function guardar(tabla, filas, conflicto) {
  if (!URL_BASE || !CLAVE) throw new Error('Faltan SUPABASE_URL o SUPABASE_SERVICE_KEY en Vercel');
  const r = await fetch(`${URL_BASE}/rest/v1/${tabla}?on_conflict=${conflicto}`, {
    method: 'POST',
    headers: { apikey: CLAVE, Authorization: `Bearer ${CLAVE}`, 'Content-Type': 'application/json', Prefer: 'resolution=merge-duplicates,return=minimal' },
    body: JSON.stringify(filas),
  });
  if (!r.ok) throw new Error(`Supabase ${r.status}: ${(await r.text()).slice(0, 300)}`);
}

// Protección opcional: si defines PORTAL_TOKEN en Vercel, el portal debe enviar
// el header "x-portal-token" con ese valor. Útil mientras no haya login de usuarios.
function autorizado(req) {
  const esperado = process.env.PORTAL_TOKEN;
  return !esperado || req.headers['x-portal-token'] === esperado;
}

function responder(res, estado, cuerpo, cacheSeg = 0) {
  res.statusCode = estado;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Cache-Control', cacheSeg ? `s-maxage=${cacheSeg}, stale-while-revalidate=${cacheSeg * 4}` : 'no-store');
  res.end(JSON.stringify(cuerpo));
}

// Filial elegida en el portal (?filial=LIMA). Por defecto LIMA.
const FILIALES = ['LIMA', 'PROVINCIAS', 'ANCASH', 'AREQUIPA', 'CAJAMARCA', 'HUANUCO', 'LA LIBERTAD', 'LAMBAYEQUE', 'PIURA'];
function filialDe(req) {
  const f = (new URL(req.url, 'http://x').searchParams.get('filial') || 'LIMA').toUpperCase();
  return FILIALES.includes(f) ? f : 'LIMA';
}

// Filtro de PostgREST por filial: PROVINCIAS = todas menos Lima.
const filtroFilial = (f) => f === 'PROVINCIAS' ? 'filial=neq.LIMA' : `filial=eq.${encodeURIComponent(f)}`;

module.exports = { consultar, guardar, autorizado, responder, filialDe, filtroFilial, FILIALES };
