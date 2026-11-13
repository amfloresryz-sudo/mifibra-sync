"""Robot que lee en ScordSoft los datos de contacto de los abonados B2B (para la web B2B).

El reporte «Cronograma de Trabajo» no trae nombre, celular ni dirección del abonado. Esos datos están en la ficha:
  Plataforma Técnica → Registro Ordenes Servicio → Filial + Nº Abonado → Buscar → Consultar → «Datos Del Abonado».

Privacidad (igual que procesar.py): solo se leen abonados del segmento B2B con instalación o avería REGISTRADA, y
los datos van directo a la tabla protegida `b2b_scord_clientes` (solo la ven los usuarios autorizados de la web B2B).
Nunca se imprimen en el registro (los registros de GitHub de este repositorio son públicos) ni se guardan capturas.

Uso:
  python -m sync.fichas_b2b --explorar     # reconocimiento: anota SOLO los nombres de los campos, no lee ni graba datos
"""
from __future__ import annotations

import argparse
import os
from datetime import date, timedelta

from playwright.sync_api import Page, sync_playwright

from .descargar import _env, iniciar_sesion
from .subir import _cabeceras, _pedir

MENU = "Registro Ordenes Servicio"


def pendientes(limite: int = 30) -> list[dict]:
    url = f"{os.environ['SUPABASE_URL'].rstrip('/')}/rest/v1/rpc/b2b_fichas_pendientes"
    return _pedir("POST", url, "Leer abonados B2B sin ficha", headers=_cabeceras(), json={"p_limite": limite}).json()


def _esperar(page: Page, ms: int = 800) -> None:
    page.wait_for_timeout(ms)
    try:
        page.wait_for_load_state("networkidle", timeout=60_000)
    except Exception:  # noqa: BLE001
        pass


# Inventario SIN valores: id, tipo y la etiqueta de la celda anterior. De los textos solo se anota el largo.
_INVENTARIO = """(raiz) => {
  const r = raiz ? document.querySelector(raiz) : document;
  if (!r) return [];
  const etiqueta = el => { const td = el.closest('td'); let p = td && td.previousElementSibling;
      while (p && !p.innerText.trim()) p = p.previousElementSibling;
      return (p ? p.innerText : '').trim().replace(/\\s+/g, ' ').slice(0, 40); };
  return [...r.querySelectorAll('input, select, textarea, button, a[id]')]
    .filter(e => e.id && e.type !== 'hidden' && e.tagName !== 'A')
    // Nada que esté dentro de una grilla de resultados (ahí las celdas vecinas pueden traer datos del cliente)
    .filter(e => !e.closest('table[id*=grv i], table[id*=gv i], table[id*=grid i], table[id*=gdv i]'))
    .map(e => { const o = {id: e.id, tag: e.tagName.toLowerCase(), tipo: e.type || '', etiqueta: etiqueta(e)};
      if (!/^[A-ZÁÉÍÓÚÑº°\\s().\\/\\-:N0-9]+$/.test(o.etiqueta)) o.etiqueta = '(no es rótulo)';
      if (e.tagName === 'SELECT') o.opciones = e.options.length;
      else if (['text','tel','email','number','textarea',''].includes(e.type)) o.largo = (e.value || '').length;
      else if (['submit','button','image'].includes(e.type)) o.texto = ((e.value || e.title || e.alt || '')).trim().slice(0, 30);
      return o; });
}"""


def _imprimir(titulo: str, items: list[dict]) -> None:
    print(f"--- {titulo}: {len(items)} controles", flush=True)
    for o in items:
        extra = " ".join(f"{k}={o[k]!r}" for k in ("opciones", "largo", "texto") if k in o)
        print(f"   {o['tag']:<8} {o['tipo']:<9} {o['id']:<60} [{o['etiqueta']}] {extra}", flush=True)


def explorar() -> None:
    base = _env("SCORDSOFT_URL", "https://mifibra.scordsoft.net").rstrip("/")
    lista = pendientes(5)
    print(f"Abonados B2B sin ficha en Supabase: {len(lista)} (se usa el 1.º para la prueba; no se imprime)", flush=True)
    with sync_playwright() as pw:
        nav = pw.chromium.launch()
        page = nav.new_page(viewport={"width": 1500, "height": 1000})
        page.set_default_timeout(60_000)
        iniciar_sesion(page, base)
        _esperar(page)
        # 1) Enlace del menú «Registro Ordenes Servicio» (se lee el href en vez de pasar el mouse por el menú)
        href = page.evaluate("""(t) => { const n = s => s.normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').replace(/\\s+/g,' ').trim().toUpperCase();
            const a = [...document.querySelectorAll('a')].find(a => n(a.innerText || a.textContent) === n(t)); return a ? a.getAttribute('href') : null; }""", MENU)
        print(f"Menú «{MENU}» → {href}", flush=True)
        if not href:
            print("No se encontró el enlace del menú.", flush=True)
            return
        page.goto(href if href.startswith("http") else f"{base}/{href.lstrip('/')}", wait_until="networkidle")
        print(f"Pantalla de búsqueda: {page.url.split('?')[0].replace(base, '')}", flush=True)
        _imprimir("Pantalla de búsqueda", page.evaluate(_INVENTARIO, "form"))
        if not lista:
            return
        c = lista[0]
        # 2) Filial + Nº abonado + fecha inicio de hace un año → Buscar (ids buscados por nombre, no fijos)
        ids = page.evaluate("""() => { const q = re => [...document.querySelectorAll('input,select')].find(e => e.id && re.test(e.id));
            const g = re => (q(re) || {}).id || null;
            return { filial: g(/filial/i), abonado: g(/abonado/i), ini: g(/fecha.*(ini|reg)|ini.*fecha/i), os: g(/n(u|ú)mero.?os|nro.?os|numeroos/i),
                     buscar: ([...document.querySelectorAll('input[type=submit],input[type=button],button,a')].find(b => /buscar/i.test(b.value || b.innerText || b.id)) || {}).id || null,
                     consultar: ([...document.querySelectorAll('input[type=submit],input[type=button],button,a')].find(b => /consultar/i.test(b.value || b.innerText || b.id)) || {}).id || null }; }""")
        print(f"Campos detectados: {ids}", flush=True)
        if ids.get("filial"):
            valor = page.evaluate("""([id, t]) => { const n = s => s.normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').trim().toUpperCase();
                const o = [...document.getElementById(id).options].find(o => n(o.text) === n(t)); return o ? o.value : null; }""", [ids["filial"], c["filial"] or ""])
            if valor and page.input_value(f"#{ids['filial']}") != valor:
                page.select_option(f"#{ids['filial']}", valor)
                _esperar(page, 1500)
            print(f"Filial elegida: {'sí' if valor else 'NO encontrada'}", flush=True)
        if ids.get("ini"):
            page.evaluate("([id, v]) => { const e = document.getElementById(id); if (e) e.value = v; }",
                          [ids["ini"], (date.today() - timedelta(days=365)).strftime("%d/%m/%Y")])
        if ids.get("abonado"):
            page.fill(f"#{ids['abonado']}", str(c["abonado_codigo"]))
        if not ids.get("buscar"):
            print("No se encontró el botón Buscar.", flush=True)
            return
        page.click(f"#{ids['buscar']}")
        _esperar(page, 2500)
        # 3) Grilla de resultados: solo estructura (encabezados, n.º de filas y controles de la 1.ª fila)
        grilla = page.evaluate("""() => { const t = [...document.querySelectorAll('table[id]')].filter(t => t.rows.length > 1)
              .sort((a, b) => b.rows.length - a.rows.length)[0];
            if (!t) return null;
            const filas = [...t.rows], cab = filas[0];
            const f1 = filas.find((r, i) => i > 0 && r.cells.length === cab.cells.length) || filas[1];
            return { id: t.id, filas: filas.length - 1, encabezados: [...cab.cells].map(c => c.innerText.trim()).slice(0, 30),
                     controles_fila1: [...f1.querySelectorAll('input,a,select')].map(e => (e.id || '') + ':' + (e.type || e.tagName)).slice(0, 15),
                     total: (document.body.innerText.match(/TOTAL DE REGISTROS:?\\s*\\d+/i) || [''])[0] }; }""")
        print(f"Grilla: {grilla}", flush=True)
        if not grilla or not ids.get("consultar"):
            return
        # 4) Elegir la 1.ª fila (radio/checkbox si hay; si no, clic en la fila) → Consultar
        elegido = page.evaluate("""(g) => { const t = document.getElementById(g); const f = [...t.rows].slice(1)
              .find(r => r.querySelector('input[type=radio],input[type=checkbox]')) || t.rows[1];
            const x = f.querySelector('input[type=radio],input[type=checkbox]');
            if (x) { x.click(); return 'control ' + x.type; } f.click(); return 'clic en la fila'; }""", grilla["id"])
        print(f"Selección de la fila: {elegido}", flush=True)
        _esperar(page, 1500)
        page.click(f"#{ids['consultar']}")
        _esperar(page, 3000)
        print(f"Tras Consultar: {page.url.split('?')[0].replace(base, '')} · ¿hay «Datos Del Abonado»? "
              f"{'sí' if page.get_by_text('Datos Del Abonado').count() else 'no'}", flush=True)
        _imprimir("Ficha (después de Consultar)", page.evaluate(_INVENTARIO, "form"))
        nav.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--explorar", action="store_true", help="solo anota los nombres de los campos (sin datos)")
    a = ap.parse_args()
    if a.explorar:
        explorar()


if __name__ == "__main__":
    main()
