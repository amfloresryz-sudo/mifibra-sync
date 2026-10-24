// GET /api/speedtest
// Tablero de Speed Test (todas las filiales): lo calcula el robot en cada sincronización con los enlaces de
// «Observaciones Tecnico» y lo guarda en la tabla "tableros" (clave "speedtest"). La página filtra en el navegador.
const { consultar, autorizado, responder, puedeVer, permitidas, puedeModulo } = require('./_supabase');

// El tablero (≈1 MB) se guarda en memoria 2 min por instancia: no se vuelve a pedir a Supabase en cada visita.
let MEM = { en: 0, d: null };
async function tablero() {
  if (MEM.d && Date.now() - MEM.en < 120e3) return MEM.d;
  const { datos } = await consultar('tableros?select=datos,actualizado&clave=eq.speedtest');
  MEM = { en: Date.now(), d: datos[0] || null }; return MEM.d;
}

module.exports = async (req, res) => {
  if (!(await autorizado(req))) return responder(res, 401, { error: 'No autorizado' });
  if (!puedeModulo(req, 'speedtest')) return responder(res, 403, { error: 'No tienes acceso a este módulo.' });
  try {
    // ?os=1,2,3: datos de esas OS de instalación para las listas de AB (F. Recep, F. atención, distrito y estado)
    const os = new URL(req.url, 'http://x').searchParams.get('os');
    if (os != null) {
      const L = [...new Set(os.split(',').map(v => parseInt(v, 10)).filter(v => v > 0))].slice(0, 300);
      if (!L.length) return responder(res, 200, []);
      const { datos } = await consultar(`ordenes_servicio?select=nro_os,abonado_codigo,filial,fecha_registro,fecha_atencion,distrito,estado_os&tipo_orden=eq.${encodeURIComponent('INSTALACION SERVICIO')}&nro_os=in.(${L.join(',')})${req.usuario.f !== 'TODAS' ? `&filial=in.(${permitidas(req.usuario.f).map(f => `"${f}"`).join(',')})` : ''}`);
      return responder(res, 200, datos);
    }
    const t = await tablero();
    if (!t) return responder(res, 404, { error: 'Aún no hay datos de Speed Test: aparecen después de la próxima actualización del robot.' });
    let d = t.datos;
    if (req.usuario.f !== 'TODAS') {   // usuario de una filial: solo sus filas (y los enlaces repetidos re-numerados)
      const nuevo = new Map(), o = [];
      d.o.forEach((f, i) => { if (puedeVer(req, d.f[f[0]])) { nuevo.set(i, o.length); o.push(f); } });
      const rep = (d.rep || []).map(([u, ix]) => [u, ix.filter(i => nuevo.has(i)).map(i => nuevo.get(i))]).filter(r => r[1].length);
      d = { ...d, o, rep };
    }
    responder(res, 200, { ...d, actualizado: t.actualizado }, 120);
  } catch (e) {
    responder(res, 500, { error: e.message });
  }
};
