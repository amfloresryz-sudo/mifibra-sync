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
      box.querySelector('.blq-a').innerHTML = [['LIMA', 'Lima'], ['PROVINCIAS', 'Provincias']].map(([k, t]) =>
        `<button type="button" class="chip" data-blq="${k}" aria-pressed="${k === 'LIMA' ? !prov : prov}">${t}</button>`).join('');
      const f = box.querySelector('.blq-f'); f.hidden = !prov;
      f.innerHTML = prov ? [['PROVINCIAS', 'Todas'], ...PROV.map(p => [p, nom(p)])].map(([k, t]) =>
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
