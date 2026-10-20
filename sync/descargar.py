"""Robot que descarga el reporte "Cronograma de Trabajo (Órdenes de Servicio)" de ScordSoft.

Repite en un navegador automático (Playwright/Chromium) los pasos manuales:
iniciar sesión, abrir el reporte, elegir filial y fechas, Procesar y descargar el Excel.

Variables de entorno (se configuran como *secrets*, nunca en el código):
  SCORDSOFT_URL            https://mifibra.scordsoft.net
  SCORDSOFT_USUARIO        usuario de servicio (no uses tu usuario personal)
  SCORDSOFT_CLAVE          contraseña de ese usuario
  SCORDSOFT_TOTP_SECRETO   (opcional) clave de la app autenticadora si el usuario tiene verificación en 2 pasos

Selectores del login (opcionales; ajústalos si el robot no encuentra los campos):
  SCORDSOFT_SEL_USUARIO, SCORDSOFT_SEL_CLAVE, SCORDSOFT_SEL_INGRESAR,
  SCORDSOFT_SEL_CODIGO, SCORDSOFT_SEL_VERIFICAR
"""
from __future__ import annotations

import os
import time
from datetime import date
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright

REPORTE = "/CReportes/Rep_OrdenServicioCronoTrabajo_Nubyx_Filter.aspx"

# Identificadores del formulario del reporte (leídos de la página real el 14/10/2026).
SEL_FILIAL = "#MainContent_ddlFilial"
SEL_FECHA_INI = "#MainContent_txtfechainicial"
SEL_FECHA_FIN = "#MainContent_txtfechafinal"
SEL_POR_REGISTRO = "#MainContent_rdbseleccion_0"
SEL_POR_EJECUCION = "#MainContent_rdbseleccion_1"
SEL_SALIDA_EXCEL = "#MainContent_rbnXls"
SEL_PROCESAR = "#MainContent_btnEjecutar"
SEL_DESCARGA = "#MainContent_lnkDownload"

# Valores del desplegable Filial.
FILIALES = {
    "ANCASH": "5", "AREQUIPA": "6", "CAJAMARCA": "7", "HUANUCO": "8",
    "LA LIBERTAD": "4", "LAMBAYEQUE": "1", "LIMA": "9", "PIURA": "2",
    "TODAS": "0",  # opción SELECCIONE: ScordSoft devuelve todas las filiales
}


class ErrorRobot(RuntimeError):
    """Fallo esperado del robot (login, sesión, reporte que no termina)."""


def _env(nombre: str, defecto: str | None = None) -> str:
    valor = os.environ.get(nombre, defecto)
    if valor is None or valor == "":
        raise ErrorRobot(f"Falta la variable de entorno {nombre}")
    return valor


def _captura(page: Page, carpeta: Path, nombre: str) -> None:
    """Guarda una captura para diagnosticar fallos (se sube como artefacto en GitHub Actions)."""
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(carpeta / f"{nombre}.png"), full_page=True)
    except Exception:
        pass


def iniciar_sesion(page: Page, base: str) -> None:
    page.goto(f"{base}/Default.aspx", wait_until="domcontentloaded")

    sel_usuario = os.environ.get("SCORDSOFT_SEL_USUARIO") or 'input[type="text"]:visible'
    sel_clave = os.environ.get("SCORDSOFT_SEL_CLAVE") or 'input[type="password"]:visible'
    sel_ingresar = os.environ.get("SCORDSOFT_SEL_INGRESAR") or (
        'input[type="submit"]:visible, button[type="submit"]:visible, input[type="image"]:visible'
    )

    page.locator(sel_usuario).first.fill(_env("SCORDSOFT_USUARIO"))
    page.locator(sel_clave).first.fill(_env("SCORDSOFT_CLAVE"))
    page.locator(sel_ingresar).first.click()
    page.wait_for_load_state("domcontentloaded")

    secreto = os.environ.get("SCORDSOFT_TOTP_SECRETO")
    if secreto:
        import pyotp

        sel_codigo = os.environ.get("SCORDSOFT_SEL_CODIGO") or 'input[type="text"]:visible, input[type="tel"]:visible, input[type="number"]:visible'
        sel_verificar = os.environ.get("SCORDSOFT_SEL_VERIFICAR") or sel_ingresar
        campo = page.locator(sel_codigo).first
        campo.wait_for(timeout=20_000)
        campo.fill(pyotp.TOTP(secreto).now())
        page.locator(sel_verificar).first.click()
        page.wait_for_load_state("domcontentloaded")


def _poner_fecha(page: Page, selector: str, valor: date) -> None:
    texto = valor.strftime("%d/%m/%Y")
    campo = page.locator(selector)
    campo.fill(texto)
    # Algunos calendarios ASP.NET ignoran fill(); se fuerza el valor y se dispara change.
    page.eval_on_selector(
        selector,
        "(el, v) => { el.value = v; el.dispatchEvent(new Event('change', {bubbles: true})); }",
        texto,
    )


def _filial_del_excel(ruta: Path) -> str | None:
    """Lee la columna Filial de las primeras filas del Excel para confirmar que es el reporte pedido."""
    try:
        from .procesar import leer

        df = leer(ruta, header=5, nrows=20, usecols=lambda c: str(c).strip() == "Filial")
        valores = df["Filial"].dropna().astype(str).str.strip().str.upper()
        return valores.iloc[0] if len(valores) else None
    except Exception:  # noqa: BLE001 - si no se puede leer, no se bloquea la descarga
        return None


_FILIALES_LEIDAS: dict[tuple[str, int, float], set[str]] = {}


def _filiales_del_excel(ruta: Path) -> set[str]:
    """Todas las filiales que trae el Excel (para saber si 'SELECCIONE' devolvió todas). Se lee una sola vez por archivo."""
    try:
        clave = (str(ruta), ruta.stat().st_size, ruta.stat().st_mtime)
    except OSError:
        return set()
    if clave not in _FILIALES_LEIDAS:
        _FILIALES_LEIDAS[clave] = _leer_filiales(ruta)
    return _FILIALES_LEIDAS[clave]


def _leer_filiales(ruta: Path) -> set[str]:
    try:
        from .procesar import leer

        df = leer(ruta, header=5, usecols=lambda c: str(c).strip() == "Filial")
        return set(df["Filial"].dropna().astype(str).str.strip().str.upper())
    except Exception:  # noqa: BLE001
        return set()


def _un_reporte(page, contexto, base: str, destino: Path, desde: date, hasta: date, filial: str, por: str,
                diagnostico: Path, espera_max_s: int, avisos: list[str], usados: set[str]) -> Path:
    """Genera y descarga un reporte en una sesión ya iniciada. Reintenta si ScordSoft entrega el archivo
    de otra filial (pasa cuando el enlace del reporte anterior aún está en pantalla)."""
    for intento in range(3):
        _generar(page, contexto, base, destino, desde, hasta, filial, por, diagnostico, espera_max_s, avisos, usados)
        if filial.upper() == "TODAS":
            traidas = _filiales_del_excel(destino)
            if len(traidas) > 1 or intento == 2:  # trae varias filiales (o ya no queda reintento)
                return destino
            print(f"  El archivo trae solo {', '.join(traidas) or 'nada'}; se vuelve a pedir ({intento + 1}/3)…", flush=True)
            time.sleep(10)
            continue
        recibido = _filial_del_excel(destino)
        if recibido in (None, filial.upper()):
            return destino
        print(f"  ScordSoft entregó {recibido} en vez de {filial}; se vuelve a pedir ({intento + 1}/3)…", flush=True)
        time.sleep(10)
    raise ErrorRobot(f"ScordSoft no entregó el reporte de {filial} (llegó {recibido}).")


def _generar(page, contexto, base: str, destino: Path, desde: date, hasta: date, filial: str, por: str,
             diagnostico: Path, espera_max_s: int, avisos: list[str], usados: set[str]) -> None:
    """Llena los filtros, pulsa Procesar y baja el Excel nuevo."""
    page.goto(f"{base}{REPORTE}", wait_until="domcontentloaded")
    if "sinsesion" in page.url or page.locator(SEL_FILIAL).count() == 0:
        iniciar_sesion(page, base)  # la sesión expiró: se vuelve a entrar una vez
        page.goto(f"{base}{REPORTE}", wait_until="domcontentloaded")
        if "sinsesion" in page.url or page.locator(SEL_FILIAL).count() == 0:
            _captura(page, diagnostico, "login_fallido")
            raise ErrorRobot("No se pudo iniciar sesión o abrir el reporte (revisa usuario, clave, 2 pasos y selectores).")

    avisos.clear()
    page.select_option(SEL_FILIAL, value=FILIALES[filial.upper()])
    page.wait_for_load_state("domcontentloaded")
    _poner_fecha(page, SEL_FECHA_INI, desde)
    _poner_fecha(page, SEL_FECHA_FIN, hasta)
    page.check(SEL_POR_REGISTRO if por == "registro" else SEL_POR_EJECUCION)
    if page.locator(SEL_SALIDA_EXCEL).count():
        page.check(SEL_SALIDA_EXCEL)

    # Recordar el enlace anterior para no descargar un reporte viejo.
    enlace_previo = page.locator(SEL_DESCARGA).get_attribute("href") if page.locator(SEL_DESCARGA).count() else None
    page.click(SEL_PROCESAR)

    limite = time.time() + espera_max_s
    href = None
    while time.time() < limite:
        if avisos:
            raise ErrorRobot(f"ScordSoft respondió: {avisos[-1]}")
        if page.locator(SEL_DESCARGA).count():
            h = page.locator(SEL_DESCARGA).get_attribute("href")
            if h and h.lower().endswith(".xlsx") and h != enlace_previo:
                href = h
                break
        time.sleep(3)
    if not href:
        _captura(page, diagnostico, f"reporte_sin_terminar_{filial.lower().replace(' ', '_')}")
        raise ErrorRobot(f"El reporte de {filial} no terminó en {espera_max_s} s.")

    url = href if href.startswith("http") else f"{base}/{href.lstrip('/')}"
    respuesta = contexto.request.get(url, timeout=300_000)
    if not respuesta.ok:
        raise ErrorRobot(f"La descarga de {filial} devolvió HTTP {respuesta.status}")
    usados.add(href)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(respuesta.body())


def descargar_reportes(
    pedidos: list[tuple[Path, date, date, str, str]],
    diagnostico: Path = Path("diagnostico"),
    espera_max_s: int = 900,
) -> list[Path]:
    """Descarga varios reportes desde un solo punto: un navegador y un solo inicio de sesión.

    pedidos: lista de (destino, desde, hasta, filial, por), con por = "registro" o "ejecucion".
    """
    base = _env("SCORDSOFT_URL", "https://mifibra.scordsoft.net").rstrip("/")
    for _, _, _, filial, _ in pedidos:
        if filial.upper() not in FILIALES:
            raise ErrorRobot(f"Filial desconocida: {filial}")

    rutas: list[Path] = []
    fallidos: list[str] = []
    with sync_playwright() as p:
        navegador = p.chromium.launch(headless=True)
        contexto = navegador.new_context(locale="es-PE", timezone_id="America/Lima", accept_downloads=True)
        page = contexto.new_page()
        page.set_default_timeout(60_000)
        avisos: list[str] = []  # mensajes emergentes de ScordSoft (p. ej. "seleccione filial")
        usados: set[str] = set()  # enlaces ya descargados en esta sesión: nunca se repiten
        page.on("dialog", lambda d: (avisos.append(d.message), d.dismiss()))
        try:
            iniciar_sesion(page, base)
            cola = list(pedidos)
            while cola:
                destino, desde, hasta, filial, por = cola.pop(0)
                t0 = time.time()
                nombre = "TODAS (filial en SELECCIONE)" if filial.upper() == "TODAS" else filial
                print(f"Descargando {nombre} {desde:%d/%m/%Y}–{hasta:%d/%m/%Y} por {por}…", flush=True)
                try:
                    ruta = _un_reporte(page, contexto, base, destino, desde, hasta, filial, por, diagnostico, espera_max_s, avisos, usados)
                    rutas.append(ruta)
                    if filial.upper() == "TODAS":
                        traidas = _filiales_del_excel(ruta)
                        print(f"  listo en {time.time() - t0:.0f} s · filiales en el archivo: {', '.join(sorted(traidas)) or 'ninguna'}", flush=True)
                        faltan = [f for f in FILIALES if f != "TODAS" and f not in traidas]
                        if faltan and len(traidas) <= 1:  # ScordSoft devolvió una sola filial: se piden las demás
                            print(f"  El archivo no trae todas las filiales; se piden aparte: {', '.join(faltan)}", flush=True)
                            cola[0:0] = [(destino.with_name(f"{destino.stem}_{i}.xlsx"), desde, hasta, f, por) for i, f in enumerate(faltan)]
                    else:
                        print(f"  listo en {time.time() - t0:.0f} s", flush=True)
                except ErrorRobot as e:  # una filial con problemas no detiene a las demás
                    fallidos.append(filial)
                    print(f"  AVISO: se omite {filial} ({por}): {e}", flush=True)
            if not rutas:
                raise ErrorRobot(f"No se pudo descargar ningún reporte ({', '.join(fallidos)}).")
            return rutas
        except PWTimeout as e:
            _captura(page, diagnostico, "tiempo_agotado")
            raise ErrorRobot(f"Tiempo agotado en el navegador: {e}") from e
        finally:
            contexto.close()
            navegador.close()


def descargar_reporte(destino: Path, desde: date, hasta: date, filial: str = "LIMA", por: str = "registro",
                      diagnostico: Path = Path("diagnostico"), espera_max_s: int = 600) -> Path:
    """Descarga un solo reporte (atajo de descargar_reportes)."""
    return descargar_reportes([(destino, desde, hasta, filial, por)], diagnostico, espera_max_s)[0]
