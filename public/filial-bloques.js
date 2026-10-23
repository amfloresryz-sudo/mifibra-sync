/* Filial en 2 bloques: Lima y Provincias (Ancash, Arequipa, Cajamarca, Huanuco, La Libertad, Lambayeque y Piura).
   Provincias muestra el total de las 7; si se quiere analizar una, se elige en su segmentador.
   Usa el <select id="filial"> de la página (oculto): al elegir, cambia su valor y avisa con «change», así cada página
   vuelve a cargar todos sus cuadros con la lógica que ya tiene. */
(function () {
  'use strict';
  const PROV = ['ANCASH', 'AREQUIPA', 'CAJAMARCA', 'HUANUCO', 'LA LIBERTAD', 'LAMBAYEQUE', 'PIURA'];
  const nom = (f) => f.split(' ').map(p => p.charAt(0) + p.slice(1).toLowerCase()).join(' ');
  function montar() {
    const sel = document.getElementById('filial'); if (!sel || sel._bloques) return; sel._bloques = true;
    sel.style.display = 'none'; sel.setAttribute('aria-hidden', 'true'); sel.tabIndex = -1;
    const box = document.createElement('div'); box.className = 'blq';
    box.innerHTML = '<div class="chips blq-a" role="group" aria-label="Lima o provincias"></div><div class="chips blq-f" role="group" aria-label="Filial de provincias"></div>';
    sel.after(box);
    const lbl = document.querySelector('label[for="filial"]'); if (lbl) lbl.removeAttribute('for');
    let ult = null;
    const pinta = () => {
      const v = sel.value || 'LIMA', prov = v !== 'LIMA'; ult = v;
      const ok = (k) => !window.mfPuede || window.mfPuede(k), fijo = !ok('LIMA') && !ok('PROVINCIAS');   // usuario de una sola provincia
      box.querySelector('.blq-a').innerHTML = fijo ? `<button type="button" class="chip" aria-pressed="true" disabled style="opacity:1;cursor:default">${nom(v)}</button>`
        : [['LIMA', 'Lima'], ['PROVINCIAS', 'Provincias']].filter(([k]) => ok(k)).map(([k, t]) =>
        `<button type="button" class="chip" data-blq="${k}" aria-pressed="${k === 'LIMA' ? !prov : prov}">${t}</button>`).join('');
      const f = box.querySelector('.blq-f'); f.hidden = !prov || fijo;
      f.innerHTML = prov && !fijo ? [['PROVINCIAS', 'Todas'], ...PROV.map(p => [p, nom(p)])].filter(([k]) => ok(k)).map(([k, t]) =>
        `<button type="button" class="chip" data-blq="${k}" aria-pressed="${v === k}">${t}</button>`).join('') : '';
    };
    box.addEventListener('click', (e) => { const b = e.target.closest('[data-blq]'); if (!b || sel.value === b.dataset.blq) return;
      sel.value = b.dataset.blq; pinta(); sel.dispatchEvent(new Event('change', { bubbles: true })); });
    sel.addEventListener('change', pinta);
    setInterval(() => { if (sel.value !== ult) pinta(); }, 400);   // la página puede cambiar la filial al cargar
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
