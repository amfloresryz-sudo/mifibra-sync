// TEMPORAL (se borra después de usarlo): copia las imágenes de ayuda del buscador FFTT anterior a la tabla tableros (clave fftt_img).
const { guardar, responder } = require('./_supabase');

module.exports = async (req, res) => {
  try {
    const r = await fetch('https://buscadorffttenvivo.vercel.app/', { cache: 'no-store' });
    const t = await r.text();
    const out = {};
    for (const m of t.matchAll(/["']?(\w+)["']?\s*:\s*["'](data:image\/(?:jpeg|png|webp);base64,[A-Za-z0-9+\/=]+)["']/g)) out[m[1]] = m[2];
    await guardar('tableros', [{ clave: 'fftt_img', datos: out, actualizado: new Date().toISOString() }], 'clave');
    responder(res, 200, { ok: true, imagenes: Object.keys(out), kb: Object.fromEntries(Object.entries(out).map(([k, v]) => [k, Math.round(v.length / 1024)])) });
  } catch (e) {
    responder(res, 500, { error: e.message });
  }
};
