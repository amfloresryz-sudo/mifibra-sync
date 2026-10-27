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
  GEMINI_MODELO    (opcional) por defecto gemini-flash-latest (si no existe, se usa el flash disponible)

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
from .manual_fotos import DIAS_TOLERANCIA, PERDIDA_MAX_DB, texto_manual

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


def buscar_instalaciones(page: Page, base: str, abonado: str) -> list[dict]:
    """Filas de INSTALACION SERVICIO INTERNET en estado DESCARGADA (ejecutadas) del AB, de la más reciente a la más antigua."""
    page.goto(f"{base}{PANTALLA_OS}", wait_until="networkidle")
    if page.input_value("#MainContent_cboFilial") != FILIAL_LIMA:
        # Cambiar la filial recarga la página (postback): hay que esperar a que termine antes de escribir el AB.
        try:
            with page.expect_navigation(wait_until="networkidle", timeout=20_000):
                page.select_option("#MainContent_cboFilial", FILIAL_LIMA)
        except Exception:  # noqa: BLE001 - si no hubo recarga, se sigue
            page.wait_for_load_state("networkidle")
    page.fill("#MainContent_txtNroAbonado", str(int(abonado)))
    page.wait_for_timeout(500)
    if page.input_value("#MainContent_txtNroAbonado").strip() != str(int(abonado)):
        page.fill("#MainContent_txtNroAbonado", str(int(abonado)))
    # Rango amplio: últimos 90 días hasta hoy (la pantalla trae por defecto solo ~4 semanas).
    from datetime import date, timedelta
    for sel, f in (("#MainContent_txtFechaInicio", date.today() - timedelta(days=90)), ("#MainContent_txtFechaTermino", date.today())):
        page.eval_on_selector(sel, "(el, v) => { el.value = v; el.dispatchEvent(new Event('change', {bubbles: true})); }",
                              f.strftime("%d/%m/%Y"))
    page.click("#MainContent_btnBuscar")
    page.wait_for_selector("input[id^=RowSelector]", timeout=60_000)
    filas = page.evaluate(
        """() => [...document.querySelectorAll('input[id^=RowSelector]')].map(r => {
            const tr = r.closest('tr');
            return {radio: r.id, celdas: [...tr.querySelectorAll('td')].map(td => td.innerText.trim())};
        })"""
    )
    salida = []
    for f in filas:
        texto = " ".join(f["celdas"])
        if TIPO_BUSCADO not in texto.upper():
            continue
        if not re.search(rf"\b0*{int(abonado)}\b", texto):  # solo filas de ese abonado
            continue
        f["texto"] = texto
        f["os"] = (re.search(r"\b(\d{8})\b", texto) or re.search(r"\b(\d{5,})\b", texto) or [None, ""])[1]
        f["descargada"] = "DESCARGADA" in texto.upper()
        print(f"   fila: {texto[:140]}", flush=True)
        salida.append(f)
    # Primero las ejecutadas (DESCARGADA); las registradas/observadas aún no tienen fotos finales.
    salida.sort(key=lambda f: not f["descargada"])
    return salida


def abrir_instalacion(page: Page, base: str, abonado: str, fila: dict) -> dict:
    """Desde la grilla ya cargada, selecciona la fila y pulsa Consultar."""
    page.click(f"#{fila['radio']}")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=60_000):
        page.locator("input[type=submit][value='Consultar'], input[type=button][value='Consultar']").first.click()
    texto = fila["texto"]
    datos = {
        "abonado": abonado,
        "os": fila["os"],
        "fila": texto,
        "fecha_ejecucion": _campo_por_etiqueta(page, "FECHA DE EJECUCION"),
        "tecnico": _campo_por_etiqueta(page, "TECNICO EJECUTOR"),
        "obs_tecnico": _campo_por_etiqueta(page, "OBS DEL TECNICO"),
        "hora_inicio": _campo_por_etiqueta(page, "HORA INICIO EJEC"),
        "hora_fin": _campo_por_etiqueta(page, "HORA TÉRMINO EJEC") or _campo_por_etiqueta(page, "HORA TERMINO EJEC"),
        "latitud": _campo_por_etiqueta(page, "LATITUD"),
        "longitud": _campo_por_etiqueta(page, "LONGITUD"),
    }
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
        "potencia_nap_dbm": {"type": "NUMBER", "nullable": True},
        "potencia_cliente_dbm": {"type": "NUMBER", "nullable": True},
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
- Foto 2: lee la potencia (dBm, valor negativo) medida en la NAP y la medida en el cliente y devuélvelas en
  potencia_nap_dbm y potencia_cliente_dbm (null si no se puede leer). La pérdida entre caja y cliente
  (|NAP − cliente|) debe ser como máximo {PERDIDA_MAX_DB} dB.
- Mapa de CTO y envío de parámetros son capturas del gestor: no requieren sello.
- Si un ítem no aparece en ninguna foto: estado FALTA.
- Si aparece pero no cumple algún punto: ERROR, y en "error" explica exactamente qué falta o está mal (breve, en español).
- Si cumple todo: OK con error vacío. Ítem MESH sin MESH en la orden: NO APLICA.
- No inventes: si algo no se puede leer en la foto, es ERROR ("no legible").
- resultado = OK solo si todos los ítems están OK o NO APLICA; si no, OBSERVADA.
- resumen: una o dos frases para el supervisor.
Responde solo con el JSON pedido."""


def _modelos_disponibles(clave: str) -> list[str]:
    """Modelos 'flash' de la clave que aceptan generateContent (del más nuevo al más antiguo)."""
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", params={"key": clave, "pageSize": 200}, timeout=60)
        r.raise_for_status()
        nombres = [m["name"].split("/", 1)[1] for m in r.json().get("models", [])
                   if "generateContent" in m.get("supportedGenerationMethods", [])]
    except Exception:  # noqa: BLE001
        return []
    malos = ("image", "tts", "audio", "live", "embedding", "thinking", "exp")
    flash = [n for n in nombres if "flash" in n and not any(m in n for m in malos)]
    # Primero los flash normales (más nuevos primero) y al final los "lite".
    return sorted(flash, key=lambda n: ("lite" in n, [-ord(c) for c in n]))


def _achicar(mime: str, cuerpo: bytes, lado: int = 1600) -> tuple[str, bytes]:
    """Reduce la foto a máx. 1600 px por lado (se sigue leyendo el sello y los dBm) para que la IA responda rápido."""
    try:
        import io

        from PIL import Image

        im = Image.open(io.BytesIO(cuerpo))
        im = im.convert("RGB")
        im.thumbnail((lado, lado))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return "image/jpeg", buf.getvalue()
    except Exception:  # noqa: BLE001 - si no se puede, se envía tal cual
        return mime, cuerpo


def validar_con_ia(datos: dict, fotos: list[tuple[str, bytes]]) -> dict:
    clave = _env("GEMINI_API_KEY")
    fotos = [_achicar(m, c) for m, c in fotos]
    print(f"   fotos para la IA: {sum(len(c) for _, c in fotos) // 1024} KB", flush=True)
    partes = [{"text": _prompt(datos, len(fotos))}]
    for i, (mime, cuerpo) in enumerate(fotos, 1):
        partes.append({"text": f"Foto {i}:"})
        partes.append({"inline_data": {"mime_type": mime or "image/jpeg", "data": base64.b64encode(cuerpo).decode()}})
    cuerpo = {
        "contents": [{"role": "user", "parts": partes}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": ESQUEMA},
    }
    preferidos = [m for m in (os.environ.get("GEMINI_MODELO"), "gemini-flash-latest") if m]
    probados: set[str] = set()
    cola = list(preferidos)
    ultimo_error = ""
    while cola:
        modelo = cola.pop(0)
        if modelo in probados:
            continue
        probados.add(modelo)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
        r = None
        for intento in range(3):
            try:
                r = requests.post(url, params={"key": clave}, json=cuerpo, timeout=180)
            except requests.exceptions.Timeout:
                r = None
                break  # tardó demasiado: se prueba otro modelo
            if r.status_code in (429, 500, 503) and intento < 2:  # límite gratuito por minuto / saturación
                time.sleep(20 * (intento + 1))
                continue
            break
        if r is None:
            ultimo_error = f"{modelo}: tiempo agotado"
            print(f"   IA: {modelo} tardó demasiado; se prueba otro modelo", flush=True)
            if not cola:
                cola += [m for m in _modelos_disponibles(clave) if m not in probados][:5]
            continue
        no_existe = r.status_code in (400, 404) and ("not found" in r.text.lower() or "not supported" in r.text.lower())
        if no_existe or r.status_code in (429, 500, 503):
            # El modelo no existe o está saturado: se prueba con otro modelo disponible en la clave.
            ultimo_error = f"{modelo}: {r.status_code}"
            print(f"   IA: {modelo} no disponible ({r.status_code}); se prueba otro modelo", flush=True)
            if not cola:
                cola += [m for m in _modelos_disponibles(clave) if m not in probados][:5]
            continue
        if not r.ok:
            raise ErrorRobot(f"Gemini ({modelo}) respondió {r.status_code}: {r.text[:300]}")
        print(f"   IA: {modelo}", flush=True)
        texto = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return revisar_perdida(json.loads(texto))
    raise ErrorRobot(f"No hay un modelo Gemini disponible para la clave ({ultimo_error})")


def revisar_perdida(v: dict) -> dict:
    """La regla de pérdida de la foto 2 se calcula aquí (no se deja al criterio de la IA)."""
    nap, cli = v.get("potencia_nap_dbm"), v.get("potencia_cliente_dbm")
    if nap is None or cli is None:
        return v
    perdida = round(abs(float(nap) - float(cli)), 2)
    v["perdida_db"] = perdida
    if perdida > PERDIDA_MAX_DB:
        for m in v.get("manual", []):
            if m.get("item") == 2:
                txt = (f"Pérdida caja–cliente de {perdida} dB (NAP {nap} dBm, cliente {cli} dBm); "
                       f"máximo permitido {PERDIDA_MAX_DB} dB.")
                m["estado"] = "ERROR"
                m["error"] = txt if not m.get("error") else f"{txt} {m['error']}"
        v["resultado"] = "OBSERVADA"
    return v


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
                filas = buscar_instalaciones(page, base, ab)
                if not filas:
                    raise ErrorRobot(f"no tiene OS de {TIPO_BUSCADO} en el rango de fechas")
                fotos = []
                for k, fila in enumerate(filas):
                    if k:  # volver a cargar la grilla para la siguiente OS
                        buscar_instalaciones(page, base, ab)
                    res["orden"] = abrir_instalacion(page, base, ab, fila)
                    if page.locator("#btnverimagen_tecnico").count() == 0:
                        print(f"   OS {fila['os']}: sin botón de capturas del técnico", flush=True)
                        continue
                    fotos = descargar_fotos(page)
                    print(f"   OS {fila['os']}: {len(fotos)} fotos", flush=True)
                    if fotos:
                        break
                res["n_fotos"] = len(fotos)
                if not fotos:
                    raise ErrorRobot("ninguna OS de instalación tiene capturas del técnico")
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
