// GET /api/indicadores?filial=LIMA
// Devuelve todo lo que necesita el Dashboard y las tarjetas de cada módulo en una sola llamada.
// El robot los calcula en cada sincronización y los guarda listos en la tabla "tableros" (clave "indicadores:<FILIAL>"):
// aquí solo se lee esa fila. Antes se calculaban en Supabase en cada visita y eso agotaba el disco del plan gratuito.
const { consultar, autorizado, responder, filialDe, filialesDe } = require('./_supabase');

// Varias filiales (?filial=LIMA,AREQUIPA): se suman los indicadores de cada una. Los conteos se suman, los % se recalculan,
// el tiempo de atención es el promedio ponderado por las averías descargadas de cada filial y el ranking junta a los técnicos.
const r1 = (v) => v == null || !isFinite(v) ? null : Math.round(v * 10) / 10;
function juntar(L) {
  const I = L.map(d => d.indicadores || {}), suma = (k) => I.reduce((a, x) => a + (+x[k] || 0), 0), o = {};
  ['total_ordenes', 'inst_total', 'inst_registradas', 'inst_descargadas', 'inst_observadas', 'inst_anuladas', 'st_total', 'st_registradas', 'st_descargadas', 'st_anuladas',
    'obs_total', 'obs_comercial', 'obs_tecnico', 'anul_total'].forEach(k => { o[k] = suma(k); });
  o.inst_efectividad_pct = o.inst_total ? r1(100 * o.inst_descargadas / o.inst_total) : null;
  const pond = (k) => { const V = I.filter(x => x[k] != null && x.st_descargadas), w = V.reduce((a, x) => a + x.st_descargadas, 0); return w ? r1(V.reduce((a, x) => a + x[k] * x.st_descargadas, 0) / w) : null; };
  o.st_horas_mediana = pond('st_horas_mediana'); o.st_horas_promedio = pond('st_horas_promedio');
  const top = (k, kn) => { const x = I.filter(y => y[k]).sort((a, b) => (b[kn] || 0) - (a[kn] || 0))[0]; o[k] = x ? x[k] : null; o[kn] = x ? x[kn] : 0; };
  top('obs_submotivo_top', 'obs_submotivo_top_n'); top('anul_motivo_top', 'anul_motivo_top_n');
  const ds = I.map(x => x.periodo_desde).filter(Boolean).sort(), hs = I.map(x => x.periodo_hasta).filter(Boolean).sort();
  o.periodo_desde = ds[0] || null; o.periodo_hasta = hs[hs.length - 1] || null; o.actualizado_en = I.map(x => x.actualizado_en).filter(Boolean).sort().pop() || null;
  const pd = {}; L.forEach(d => (d.por_dia || []).forEach(x => { const t = pd[x.dia] || (pd[x.dia] = { dia: x.dia, instalaciones: 0, servicio_tecnico: 0 }); t.instalaciones += x.instalaciones || 0; t.servicio_tecnico += x.servicio_tecnico || 0; }));
  const pe = {}; L.forEach(d => Object.entries(d.por_estado || {}).forEach(([k, v]) => { pe[k] = (pe[k] || 0) + v; }));
  const rk = L.flatMap(d => d.ranking_tecnicos || []).sort((a, b) => b.instalaciones - a.instalaciones || b.total - a.total || String(a.tecnico_ejecutor).localeCompare(b.tecnico_ejecutor)).slice(0, 10);
  return { indicadores: o, por_dia: Object.values(pd).sort((a, b) => a.dia < b.dia ? -1 : 1), por_estado: pe, ranking_tecnicos: rk };
}

module.exports = async (req, res) => {
  if (!(await autorizado(req))) return responder(res, 401, { error: 'No autorizado' });
  const lista = filialesDe(req);
  if (lista.length > 1) {
    try {
      const claves = lista.map(f => encodeURIComponent('"indicadores:' + f + '"')).join(',');
      const { datos } = await consultar(`tableros?select=clave,datos,actualizado&clave=in.(${claves})`);
      if (!datos.length) return responder(res, 404, { error: 'Aún no hay indicadores para esas filiales: espera la próxima actualización del robot.' });
      return responder(res, 200, { filial: lista.join(','), ...juntar(datos.map(x => x.datos || {})), actualizado: datos.map(x => x.actualizado).sort().pop() }, 120);
    } catch (e) { return responder(res, 500, { error: e.message }); }
  }
  const filial = filialDe(req);
  try {
    const { datos } = await consultar(`tableros?select=datos,actualizado&clave=eq.${encodeURIComponent('indicadores:' + filial)}`);
    if (!datos.length) return responder(res, 404, { error: `Aún no hay indicadores para la filial ${filial}: espera la próxima actualización del robot.` });
    const d = datos[0].datos || {};
    responder(res, 200, {
      filial,
      indicadores: d.indicadores || null,
      por_dia: d.por_dia || [],
      por_estado: d.por_estado || {},
      ranking_tecnicos: d.ranking_tecnicos || [],
      actualizado: datos[0].actualizado,
    }, 120); // caché de 2 minutos en el borde de Vercel (el robot actualiza cada 20 min)
  } catch (e) {
    responder(res, 500, { error: e.message });
  }
};
