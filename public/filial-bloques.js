/* Filial en 2 bloques: Lima y Provincias (Ancash, Arequipa, Cajamarca, Huanuco, La Libertad, Lambayeque y Piura).
   Provincias muestra el total de las 7; si se quiere analizar una, se elige en su segmentador.
   Usa el <select id="filial"> de la página (oculto): al elegir, cambia su valor y avisa con «change», así cada página
   vuelve a cargar todos sus cuadros con la lógica que ya tiene. */
/* Varias filiales a la vez (Lima + provincias, o algunas provincias): el valor del <select id="filial"> es la lista
   separada por comas («LIMA,AREQUIPA,PIURA»; «PROVINCIAS» = las 7). window.MFF tiene lo que usan las páginas para traer
   el tablero de cada filial elegida y juntarlos en uno (analizado por filial, como Provincias › Todas). */
window.MFF = (function () {
  const PROV = ['ANCASH', 'AREQUIPA', 'CAJAMARCA', 'HUANUCO', 'LA LIBERTAD', 'LAMBAYEQUE', 'PIURA'];
  const partes = (v) => String(v || 'LIMA').split(',').filter(Boolean).flatMap(f => f === 'PROVINCIAS' ? PROV : [f]);
  const esCombo = (v) => String(v || '').includes(',');
  const ok = (k) => !window.mfPuede || window.mfPuede(k);
  // Lista → valor: 'LIMA' · 'AREQUIPA' · 'PROVINCIAS' (las 7) · 'LIMA,PROVINCIAS' · 'LIMA,AREQUIPA,PIURA' …
  const canon = (L) => { const lima = L.includes('LIMA'), pv = PROV.filter(p => L.includes(p)), todas = pv.length === PROV.length && ok('PROVINCIAS');
    const t = [...(lima ? ['LIMA'] : []), ...(todas ? ['PROVINCIAS'] : pv)]; return t.join(',') || 'LIMA'; };
  const nom = (f) => String(f).split(' ').map(p => p.charAt(0) + p.slice(1).toLowerCase()).join(' ');
  const txt = (v) => { const L = partes(v), pv = L.filter(f => f !== 'LIMA');
    return [...(L.includes('LIMA') ? ['Lima'] : []), ...(pv.length === PROV.length ? ['Provincias'] : pv.map(nom))].join(' + '); };
  // Junta los tableros de cada filial: las filas llevan su filial en «z» (la zona pasa a ser la filial) y, si se pide, en fiKey.
  function unir(L, opts = {}) {
    const o = { combo: true, sel: L.map(([f]) => f) };
    const arrZ = opts.arrZ || {}, fiKey = opts.fiKey;
    L.forEach(([f, d]) => { if (!d) return;
      Object.keys(d).forEach(k => { const v = d[k];
        if (Array.isArray(v)) {
          const A = o[k] || (o[k] = []);
          v.forEach(x => { if (Array.isArray(x)) { if (arrZ[k] != null) x[arrZ[k]] = f; }
            else if (x && typeof x === 'object') { x.z = f; if (fiKey) x[fiKey] = f; }
            A.push(x); });
        } else if (k === 'zonas' && v && typeof v === 'object') { const Z = o.zonas || (o.zonas = {}); Object.keys(v).forEach(di => { if (!(di in Z)) Z[di] = f; }); }
        else if (k === 'pdia' && v && typeof v === 'object') { const P = o.pdia || (o.pdia = {}); Object.keys(v).forEach(di => { P[di] = (P[di] || 0) + (+v[di] || 0); }); }
        else if (k === 'avt' && v && typeof v === 'object') unirAvt(o, f, v);
        else if (k === 'bini' && v && typeof v === 'object') {   // fotos de la bolsa inicial: se juntan por día (zona y filial = la filial)
          const B = o.bini || (o.bini = {});
          Object.keys(v).forEach(d => { const x = v[d] || {}, y = B[d] || (B[d] = { c: x.c, f: [] });
            if (x.c && (!y.c || x.c > y.c)) y.c = x.c;
            (x.f || []).forEach(r => { const q = r.slice(); q[5] = f; q[6] = f; y.f.push(q); }); }); }
        else if (k === 'config' && v && typeof v === 'object') { const c = o.config || (o.config = { ...v, meta_mes: 0 }); c.meta_mes = (+c.meta_mes || 0) + (+v.meta_mes || 0); }
        else if (k === 'corte' || k === 'actualizado') { if (v && (!o[k] || v > o[k])) o[k] = v; }
        else if (k === 'dias') o.dias = Math.max(o.dias || 0, +v || 0);
        else if (v && typeof v === 'object') o[k] = { ...(o[k] || {}), ...v };
        else if (!(k in o)) o[k] = v; }); });
    o.filial = canon(o.sel);
    return o;
  }
  // Avance total: tablas de nombres + filas con índices; se rehacen los índices al juntar
  function unirAvt(o, f, A) {
    const B = o.avt || (o.avt = { t: [], s: [], e: [], k: [], di: [], fi: [], r: [] }), ix = {};
    ['t', 's', 'e', 'k', 'di', 'fi'].forEach(c => { ix[c] = new Map(B[c].map((v, i) => [v, i])); });
    const id = (c, v) => { if (!ix[c].has(v)) { ix[c].set(v, B[c].length); B[c].push(v); } return ix[c].get(v); };
    const R = A.r || (A.o || []).map(x => [x[0], x[1], x[2], x[3], x[4], 1, x[5], x[6], x[7], x[8], x[9], x[10]]);
    R.forEach(x => { B.r.push([x[0], id('t', A.t[x[1]]), id('s', A.s[x[2]]), id('e', A.e[x[3]]), id('k', A.k[x[4]]), x[5], x[6], x[7], x[8], x[9], id('di', (A.di || [])[x[10]] ?? ''), id('fi', f)]); });
  }
  // Trae el tablero de cada filial (url(f) → dirección) y los junta
  async function traerVarias(v, url, opts = {}) {
    const L = partes(v), h = { headers: { 'x-portal-token': (() => { try { return sessionStorage.getItem('mifibra_portal_token') || ''; } catch { return ''; } })() } };
    const R = await Promise.all(L.map(async f => { const r = await fetch(url(f), h); if (r.status === 401) { location.href = '/'; throw new Error('Sin acceso'); }
      const j = await r.json().catch(() => ({})); if (!r.ok) { if (r.status === 404) return [f, null]; throw new Error(j.error || `Error ${r.status}`); } return [f, j]; }));
    if (!R.some(([, d]) => d)) throw Object.assign(new Error('Aún no hay datos de esas filiales.'), { status: 404 });
    return unir(R, opts);
  }
  return { PROV, partes, esCombo, canon, nom, txt, unir, traerVarias, ok };
})();

/* Filial en 2 bloques: Lima y Provincias (Ancash, Arequipa, Cajamarca, Huanuco, La Libertad, Lambayeque y Piura).
   Se pueden elegir varias a la vez: Lima + Provincias, Lima + algunas provincias o solo algunas provincias.
   Usa el <select id="filial"> de la página (oculto): al elegir, cambia su valor y avisa con «change», así cada página
   vuelve a cargar todos sus cuadros con la lógica que ya tiene. */
(function () {
  'use strict';
  const M = window.MFF, PROV = M.PROV, nom = M.nom;
  function montar() {
    const sel = document.getElementById('filial'); if (!sel || sel._bloques) return; sel._bloques = true;
    sel.style.display = 'none'; sel.setAttribute('aria-hidden', 'true'); sel.tabIndex = -1;
    const box = document.createElement('div'); box.className = 'blq';
    box.innerHTML = '<div class="chips blq-a" role="group" aria-label="Lima o provincias"></div><div class="chips blq-f" role="group" aria-label="Filiales de provincias"></div><span class="blq-n"></span>';
    sel.after(box);
    const lbl = document.querySelector('label[for="filial"]'); if (lbl) lbl.removeAttribute('for');
    let ult = null, elegidas = null, tEnv = null;
    const fijar = (v) => { if (![...sel.options].some(o => o.value === v)) { const op = document.createElement('option'); op.value = v; op.textContent = v; sel.appendChild(op); } sel.value = v; };
    const pinta = () => {
      const v = sel.value || 'LIMA'; ult = v; const L = elegidas || M.partes(v);
      const ok = M.ok, provOk = PROV.filter(ok), fijo = (ok('LIMA') ? 1 : 0) + provOk.length <= 1;
      const pv = L.filter(f => f !== 'LIMA');
      box.querySelector('.blq-a').innerHTML = fijo ? `<button type="button" class="chip" aria-pressed="true" disabled style="opacity:1;cursor:default">${nom(v)}</button>`
        : [['LIMA', 'Lima'], ['PROV', provOk.length === 1 ? nom(provOk[0]) : 'Provincias']].filter(([k]) => k === 'LIMA' ? ok(k) : provOk.length).map(([k, t]) =>
        `<button type="button" class="chip" data-blq="${k}" aria-pressed="${k === 'LIMA' ? L.includes('LIMA') : pv.length > 0}">${t}</button>`).join('');
      const f = box.querySelector('.blq-f'), ver = !fijo && pv.length > 0 && provOk.length > 1; f.hidden = !ver;
      f.innerHTML = ver ? `<button type="button" class="chip" data-blq="TODAS" aria-pressed="${provOk.every(p => pv.includes(p))}">Todas</button>` + provOk.map(p =>
        `<button type="button" class="chip" data-blq="${p}" aria-pressed="${pv.includes(p)}">${nom(p)}</button>`).join('') : '';
      box.querySelector('.blq-n').textContent = elegidas ? 'Cargando…' : '';
    };
    box.addEventListener('click', (e) => { const b = e.target.closest('[data-blq]'); if (!b) return;
      const provOk = PROV.filter(M.ok), S = new Set(elegidas || M.partes(sel.value)), k = b.dataset.blq;
      if (k === 'LIMA') S.has('LIMA') ? S.delete('LIMA') : S.add('LIMA');
      else if (k === 'PROV') { if (provOk.some(p => S.has(p))) provOk.forEach(p => S.delete(p)); else provOk.forEach(p => S.add(p)); }
      else if (k === 'TODAS') provOk.forEach(p => S.add(p));
      else S.has(k) ? S.delete(k) : S.add(k);
      if (!S.size) return;   // siempre queda al menos una filial
      elegidas = [...S]; pinta();
      // Se espera un momento por si se marcan varias seguidas (cada cambio vuelve a traer los datos)
      clearTimeout(tEnv); tEnv = setTimeout(() => { const v = M.canon(elegidas); elegidas = null; if (v === sel.value) { pinta(); return; } fijar(v); pinta(); sel.dispatchEvent(new Event('change', { bubbles: true })); }, 700);
    });
    sel.addEventListener('change', () => { if (!elegidas) pinta(); });
    setInterval(() => { if (!elegidas && sel.value !== ult) pinta(); }, 400);   // la página puede cambiar la filial al cargar
    // La página puede fijar un valor combinado que aún no está entre las opciones
    const desc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
    Object.defineProperty(sel, 'value', { get() { return desc.get.call(this); }, set(v) { if (v && ![...this.options].some(o => o.value === v)) { const op = document.createElement('option'); op.value = v; op.textContent = v; this.appendChild(op); } desc.set.call(this, v); } });
    pinta();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', montar); else montar();
})();

/* Provincias › Todas: los cuadros se leen por filial, así que «EECC» y «Zona» en títulos, encabezados y segmentadores
   pasan a decir «Filial». Cada página indica cuándo aplica con window.esProvTodas(). */
(function () {
  'use strict';
  const REGLAS = [[/de EECC\b/g, 'de E​ECC'], [/\b1 EECC\b/g, '1 filial'], [/(\d+) EECC\b/g, '$1 filiales'], [/\bEECC_F\b/g, 'Filial'], [/\bpor EECC\b/gi, 'por filial'], [/\bEECC\b/g, 'Filial'],
    [/\bpor zona\b/gi, 'por filial'], [/\bZONAS\b/g, 'FILIALES'], [/\bZonas\b/g, 'Filiales'], [/\bZONA\b/g, 'FILIAL'], [/\bZona\b/g, 'Filial'], [/\bzona\b/g, 'filial']];
  const activo = () => typeof window.esProvTodas === 'function' && window.esProvTodas();
  function renombrar() {
    const raiz = document.querySelector('main'); if (!raiz || !activo()) return;
    const w = document.createTreeWalker(raiz, NodeFilter.SHOW_TEXT, { acceptNode: (t) => {
      if (!/EECC|zona/i.test(t.nodeValue)) return NodeFilter.FILTER_REJECT;
      const b = t.parentElement && t.parentElement.closest('button, select, option, input, textarea, script, style');
      return b && !b.matches('.avt-z, [role="tab"], .chip') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT; } });
    const L = []; let t; while ((t = w.nextNode())) L.push(t);
    L.forEach(t => { let v = t.nodeValue; REGLAS.forEach(([re, r]) => v = v.replace(re, r)); if (v !== t.nodeValue) t.nodeValue = v; });
  }
  let pend = false;
  const prog = () => { if (pend || !activo()) return; pend = true; requestAnimationFrame(() => { pend = false; renombrar(); }); };
  const iniciar = () => new MutationObserver(prog).observe(document.body, { childList: true, subtree: true, characterData: true });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar); else iniciar();
})();
