"""Robot que valida las fotos de liquidación de INSTALACIONES en ScordSoft contra el manual de Operaciones.

Pasos (los mismos que a mano):
  1. Plataforma Técnica → Registro Órdenes Servicio (Mantenimiento de Órdenes de Servicio).
  2. Filial LIMA, Nº Abonado = AB, Buscar.
  3. Selecciona la OS "INSTALACION SERVICIO INTERNET" y Consultar.
  4. "Capturas del técnico" → Ver Imágenes: descarga todas las fotos (solo en memoria, nunca se guardan en el repo).
  5. Una IA con visión (Gemini, nivel gratuito) revisa las fotos contra el manual y devuelve OK u OBSERVADA con el
     error de cada foto.

Uso:
  python -m sync.fotos 28770                 # uno o varios AB separados por espacio
  python -m sync.fotos 28770 --ver           # abre el navegador visible (para probar en tu PC)

Variables de entorno: las mismas de descargar.py (SCORDSOFT_*) más
  GEMINI_API_KEY   clave gratuita de https://aistudio.google.com/apikey
  GEMINI_MODELO    (opcional) por defecto gemini-2.5-flash

Salida: resultados_fotos/<AB>.json y resultados_fotos/reporte.html (en GitHub Actions se suben como artefacto).
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import Page, sync_playwright

from .descargar import ErrorRobot, _captura, _env, iniciar_sesion
from .manual_fotos import DIAS_TOLERANCIA, texto_manual

PANTALLA_OS = "/CTecnico/TECMantOrdenServicio.aspx"
SALIDA = Path(os.environ.get("SALIDA_FOTOS", "resultados_fotos"))
FILIAL_LIMA = "9"
TIPO_BUSCADO = "INSTALACION SERVICIO INTERNET"


# ----------------------------------------------------------------------------------------------- ScordSoft

def _campo_por_etiqueta(page: Page, etiqueta: str) -> str:
    """Valor del input/textarea/select que está al lado de una etiqueta (p. ej. 'FECHA DE EJECUCION')."""
    return page.evaluate(
        """(et) => {
            const norm = s => (s || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toUpperCase().trim();
            for (const td of document.querySelectorAll('td, label, span, div')) {
                if (td.children.length > 2 || norm(td.textContent) !== norm(et)) continue;
                let n = td.nextElementSibling;
                for (let i = 0; n && i < 3; i++, n = n.nextElementSibling) {
                    const c = n.matches('input,textarea,select') ? n : n.querySelector('input:not([type=hidden]),textarea,select');
                    if (c) return c.tagName === 'SELECT' ? (c.options[c.selectedIndex] || {}).text || '' : c.value;
                }
            }
            return '';
        }""",
        etiqueta,
    ) or ""


def abrir_instalacion(page: Page, base: str, abonado: str) -> dict:
    page.goto(f"{base}{PANTALLA_OS}", wait_until="domcontentloaded")
    page.select_option("#MainContent_cboFilial", FILIAL_LIMA)
    page.wait_for_load_state("domcontentloaded")
    page.fill("#MainContent_txtNroAbonado", str(int(abonado)))
    page.click("#MainContent_btnBuscar")
    page.wait_for_selector("input[id^=RowSelector]", timeout=60_000)

    filas = page.evaluate(
        """() => [...document.querySelectorAll('input[id^=RowSelector]')].map(r => {
            const tr = r.closest('tr');
            return {radio: r.id, celdas: [...tr.querySelectorAll('td')].map(td => td.innerText.trim())};
        })"""
    )
    candidatas = [f for f in filas if TIPO_BUSCADO in " ".join(f["celdas"]).upper()
                  and "ANULADA" not in " ".join(f["celdas"]).upper()]
    if not candidatas:
        raise ErrorRobot(f"AB {abonado}: no tiene OS de {TIPO_BUSCADO} en el rango de fechas")
    fila = candidatas[0]  # la grilla viene de la más reciente a la más antigua
    texto = " ".join(fila["celdas"])
    os_num = (re.search(r"\b(\d{6,})\b", texto) or [None, ""])[1]

    page.click(f"#{fila['radio']}")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=60_000):
        page.locator("input[type=submit][value='Consultar'], input[type=button][value='Consultar']").first.click()

    datos = {
        "abonado": abonado,
        "os": os_num,
        "fila": texto,
        "fecha_ejecucion": _campo_por_etiqueta(page, "FECHA DE EJECUCION"),
        "tecnico": _campo_por_etiqueta(page, "TECNICO EJECUTOR"),
        "obs_tecnico": _campo_por_etiqueta(page, "OBS DEL TECNICO"),
        "hora_inicio": _campo_por_etiqueta(page, "HORA INICIO EJEC"),
        "hora_fin": _campo_por_etiqueta(page, "HORA TÉRMINO EJEC") or _campo_por_etiqueta(page, "HORA TERMINO EJEC"),
        "latitud": _campo_por_etiqueta(page, "LATITUD"),
        "longitud": _campo_por_etiqueta(page, "LONGITUD"),
    }
    # Dirección del abonado: se toma de la grilla (columna DIRECCION).
    datos["direccion"] = fila["celdas"][-2] if len(fila["celdas"]) >= 2 else ""
    return datos


def descargar_fotos(page: Page, espera_s: int = 40) -> list[tuple[str, bytes]]:
    """Abre 'Capturas del técnico' y recoge las imágenes que la galería pide a downloadpageOS.aspx."""
    fotos: list[tuple[str, bytes]] = []
    vistas: set[str] = set()

    def al_responder(resp):
        if "downloadpageOS.aspx" in resp.url and resp.url not in vistas and resp.ok:
            vistas.add(resp.url)
            try:
                fotos.append((resp.headers.get("content-type", "image/jpeg").split(";")[0], resp.body()))
            except Exception:  # noqa: BLE001
                pass

    page.on("response", al_responder)
    page.click("#btnverimagen_tecnico")
    # Espera a que deje de llegar fotos durante 4 s seguidos.
    fin, ultimo, cuenta = time.time() + espera_s, time.time(), 0
    while time.time() < fin:
        page.wait_for_timeout(500)
        if len(fotos) != cuenta:
            cuenta, ultimo = len(fotos), time.time()
        elif cuenta and time.time() - ultimo > 4:
            break
    page.remove_listener("response", al_responder)
    return fotos


# ----------------------------------------------------------------------------------------------- IA (Gemini)

ESQUEMA = {
    "type": "OBJECT",
    "properties": {
        "fotos": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "foto": {"type": "INTEGER"},
            "que_muestra": {"type": "STRING"},
            "fecha_sello": {"type": "STRING"},
            "direccion_sello": {"type": "STRING"},
            "items_manual": {"type": "ARRAY", "items": {"type": "INTEGER"}},
        }, "required": ["foto", "que_muestra", "items_manual"]}},
        "manual": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "item": {"type": "INTEGER"},
            "nombre": {"type": "STRING"},
            "estado": {"type": "STRING", "enum": ["OK", "ERROR", "FALTA", "NO APLICA"]},
            "fotos": {"type": "ARRAY", "items": {"type": "INTEGER"}},
            "error": {"type": "STRING"},
        }, "required": ["item", "nombre", "estado", "fotos", "error"]}},
        "resultado": {"type": "STRING", "enum": ["OK", "OBSERVADA"]},
        "resumen": {"type": "STRING"},
    },
    "required": ["fotos", "manual", "resultado", "resumen"],
}


def _prompt(datos: dict, n_fotos: int) -> str:
    con_mesh = "MESH" in (datos.get("obs_tecnico", "") + datos.get("fila", "")).upper()
    return f"""Eres auditor de calidad de instalaciones de fibra óptica (MiFibra, Lima). Revisa las {n_fotos} fotos adjuntas
(en orden: foto 1, foto 2, …) que subió el técnico para liquidar esta orden y compáralas con el MANUAL.

Datos de la orden:
- OS: {datos.get('os')}  · Abonado: {datos.get('abonado')}
- Dirección del cliente: {datos.get('direccion')}
- Fecha de ejecución: {datos.get('fecha_ejecucion')}  (hora inicio {datos.get('hora_inicio')}, término {datos.get('hora_fin')})
- Técnico: {datos.get('tecnico')}
- Observación del técnico: {datos.get('obs_tecnico')}
- La orden {'SÍ' if con_mesh else 'NO'} tiene MESH.

MANUAL (cada ítem es una foto obligatoria; una foto subida puede ser un collage que cubra varios ítems):
{texto_manual(con_mesh)}

Reglas:
- Fecha del sello: válida si es el día de ejecución o hasta {DIAS_TOLERANCIA} día antes. Más antigua o posterior = ERROR.
- Mapa de CTO y envío de parámetros son capturas del gestor: no requieren sello.
- Si un ítem no aparece en ninguna foto: estado FALTA.
- Si aparece pero no cumple algún punto: ERROR, y en "error" explica exactamente qué falta o está mal (breve, en español).
- Si cumple todo: OK con error vacío. Ítem MESH sin MESH en la orden: NO APLICA.
- No inventes: si algo no se puede leer en la foto, es ERROR ("no legible").
- resultado = OK solo si todos los ítems están OK o NO APLICA; si no, OBSERVADA.
- resumen: una o dos frases para el supervisor.
Responde solo con el JSON pedido."""


def validar_con_ia(datos: dict, fotos: list[tuple[str, bytes]]) -> dict:
    clave = _env("GEMINI_API_KEY")
    modelo = os.environ.get("GEMINI_MODELO") or "gemini-2.5-flash"
    partes = [{"text": _prompt(datos, len(fotos))}]
    for i, (mime, cuerpo) in enumerate(fotos, 1):
        partes.append({"text": f"Foto {i}:"})
        partes.append({"inline_data": {"mime_type": mime or "image/jpeg", "data": base64.b64encode(cuerpo).decode()}})
    cuerpo = {
        "contents": [{"role": "user", "parts": partes}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": ESQUEMA},
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
    for intento in range(4):
        r = requests.post(url, params={"key": clave}, json=cuerpo, timeout=180)
        if r.status_code in (429, 500, 503) and intento < 3:  # límite gratuito por minuto / saturación
            time.sleep(30 * (intento + 1))
            continue
        r.raise_for_status()
        texto = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(texto)
    raise ErrorRobot("Gemini no respondió")


# ----------------------------------------------------------------------------------------------- reporte

def _reporte_html(resultados: list[dict]) -> str:
    color = {"OK": "#16a34a", "ERROR": "#dc2626", "FALTA": "#b45309", "NO APLICA": "#6b7280", "OBSERVADA": "#dc2626"}
    bloques = []
    for res in resultados:
        d, v = res["orden"], res.get("validacion") or {}
        estado = v.get("resultado") or "SIN VALIDAR"
        filas = "".join(
            f"<tr><td>{m['item']}</td><td>{html.escape(m['nombre'])}</td>"
            f"<td style='color:{color.get(m['estado'], '#000')};font-weight:700'>{m['estado']}</td>"
            f"<td>{', '.join(map(str, m.get('fotos') or [])) or '—'}</td><td>{html.escape(m.get('error') or '')}</td></tr>"
            for m in v.get("manual", [])
        )
        bloques.append(
            f"<section><h2>AB {html.escape(d['abonado'])} · OS {html.escape(d.get('os') or '')} "
            f"<span style='color:{color.get(estado, '#000')}'>{estado}</span></h2>"
            f"<p>Ejecución {html.escape(d.get('fecha_ejecucion') or '')} · {html.escape(d.get('tecnico') or '')} · "
            f"{res.get('n_fotos', 0)} fotos</p><p><b>{html.escape(v.get('resumen') or res.get('error') or '')}</b></p>"
            f"<table><tr><th>#</th><th>Foto del manual</th><th>Estado</th><th>Fotos subidas</th><th>Error</th></tr>{filas}</table></section>"
        )
    return (
        "<!doctype html><meta charset=utf-8><title>Validación de fotos</title><style>"
        "body{font:14px system-ui;margin:24px;color:#111}table{border-collapse:collapse;width:100%}"
        "td,th{border:1px solid #ddd;padding:6px;text-align:left;vertical-align:top}th{background:#f3f4f6}</style>"
        f"<h1>Validación de fotos de instalación</h1><p>{datetime.now():%d/%m/%Y %H:%M}</p>{''.join(bloques)}"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("abonados", nargs="+", help="Nº de abonado (uno o varios)")
    ap.add_argument("--ver", action="store_true", help="navegador visible")
    args = ap.parse_args(argv)

    base = _env("SCORDSOFT_URL", "https://mifibra.scordsoft.net").rstrip("/")
    SALIDA.mkdir(parents=True, exist_ok=True)
    resultados = []
    with sync_playwright() as pw:
        nav = pw.chromium.launch(headless=not args.ver)
        page = nav.new_context(viewport={"width": 1400, "height": 900}).new_page()
        iniciar_sesion(page, base)
        for ab in args.abonados:
            ab = re.sub(r"\D", "", ab)
            res: dict = {"orden": {"abonado": ab}}
            try:
                res["orden"] = abrir_instalacion(page, base, ab)
                fotos = descargar_fotos(page)
                res["n_fotos"] = len(fotos)
                if not fotos:
                    raise ErrorRobot("la OS no tiene capturas del técnico")
                res["validacion"] = validar_con_ia(res["orden"], fotos)
            except Exception as e:  # noqa: BLE001 - se reporta y sigue con el siguiente AB
                res["error"] = str(e)
                _captura(page, SALIDA / "diagnostico", f"ab_{ab}")
            v = res.get("validacion") or {}
            print(f"AB {ab} · OS {res['orden'].get('os', '?')}: {v.get('resultado', 'ERROR')} — "
                  f"{v.get('resumen') or res.get('error', '')}", flush=True)
            for m in v.get("manual", []):
                if m["estado"] not in ("OK", "NO APLICA"):
                    print(f"   Foto {m['item']} {m['nombre']}: {m['estado']} — {m['error']}", flush=True)
            (SALIDA / f"{ab}.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
            resultados.append(res)
        nav.close()
    (SALIDA / "reporte.html").write_text(_reporte_html(resultados), encoding="utf-8")
    return 0 if all("error" not in r for r in resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
