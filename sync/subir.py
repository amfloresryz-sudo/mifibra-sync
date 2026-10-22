"""Escribe en Supabase (API REST / PostgREST) solo lo que cambió.

El plan gratuito de Supabase tiene poco disco: si en cada corrida se reescriben o se releen las ~400 mil órdenes del
periodo, se agota el presupuesto de E/S y el portal deja de responder. Por eso:

- El robot guarda la "huella" (hash) de cada orden en un archivo de estado que GitHub Actions conserva entre corridas
  (actions/cache). Solo se envían a Supabase las órdenes nuevas o que cambiaron.
- Los tableros e indicadores también se comparan por huella y solo se reescriben si cambiaron.
- Las órdenes que ya no están en el reporte (p. ej. al cambiar de mes) se borran.
- Si no hay archivo de estado (primera corrida o caché vencida), se arma leyendo solo id y fecha de la base.

Variables de entorno:
  SUPABASE_URL           https://<proyecto>.supabase.co
  SUPABASE_SERVICE_KEY   service_role key (solo en servidores; nunca en el navegador)
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

LOTE = 250            # filas por escritura (lotes chicos: Supabase gratuito responde mejor)
LOTE_BORRADO = 400    # ids por borrado
SIN_HUELLA = ("sincronizado_en", "huella", "cajas_cerca", "detalle_os")   # detalle_os solo sirve para calcular «detalle» (que sí cuenta): así no se reescribe toda la base
ESTADO_VERSION = 2


# ---------------------------------------------------------------- utilidades HTTP

def _cabeceras(extra: dict | None = None) -> dict:
    clave = os.environ["SUPABASE_SERVICE_KEY"]
    h = {"apikey": clave, "Authorization": f"Bearer {clave}", "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def _url(tabla: str) -> str:
    return f"{os.environ['SUPABASE_URL'].rstrip('/')}/rest/v1/{tabla}"


def _pedir(metodo: str, url: str, que: str, **kw) -> requests.Response:
    """Petición con 3 intentos (espera 20 s y 40 s): Supabase gratuito a veces tarda en responder."""
    error = ""
    for intento in range(3):
        try:
            r = requests.request(metodo, url, timeout=120, **kw)
            if r.status_code < 300:
                return r
            error = f"Supabase respondió {r.status_code}: {r.text[:300]}"
            if r.status_code in (400, 401, 403, 404, 409):  # error de datos o permisos: reintentar no sirve
                break
        except requests.RequestException as e:
            error = str(e)
        if intento < 2:
            print(f"  {que}: reintento {intento + 1} ({error[:120]})", flush=True)
            time.sleep(20 * (intento + 1))
    raise RuntimeError(f"{que}: {error}")


def leer_por_clave(ruta: str, clave: str = "id_os", por_pagina: int = 1000) -> list[dict]:
    """Lee todas las filas de una consulta, paginando por la clave (id_os > último)."""
    filas: list[dict] = []
    ultimo = None
    while True:
        filtro = f"&{clave}=gt.{ultimo}" if ultimo is not None else ""
        sep = "&" if "?" in ruta else "?"
        r = _pedir("GET", _url(f"{ruta}{filtro}{sep}order={clave}.asc&limit={por_pagina}"), "Lectura", headers=_cabeceras())
        lote = r.json()
        filas.extend(lote)
        if len(lote) < por_pagina:
            return filas
        ultimo = lote[-1][clave]


# ---------------------------------------------------------------- estado entre corridas

def _hash(obj) -> str:
    return hashlib.md5(json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()[:16]


def huella(f: dict) -> str:
    """Huella del contenido de la orden, sin la hora de sincronización ni las cajas cercanas (van aparte)."""
    return _hash({k: v for k, v in f.items() if k not in SIN_HUELLA})


def cargar_estado(ruta: Path) -> dict | None:
    try:
        with gzip.open(ruta, "rt", encoding="utf-8") as fh:
            e = json.load(fh)
        if e.get("v") != ESTADO_VERSION:
            return None
        e["o"] = {int(k): v for k, v in e.get("o", {}).items()}
        return e
    except FileNotFoundError:
        return None
    except Exception as ex:  # noqa: BLE001 - un archivo dañado no detiene el robot: se rearma
        print(f"Aviso: el archivo de estado no se pudo leer ({ex}); se rearma.", flush=True)
        return None


def guardar_estado(ruta: Path, estado: dict) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(estado, fh, separators=(",", ":"))
    tmp.replace(ruta)


def estado_desde_base() -> dict:
    """Primera corrida sin archivo de estado: id y fecha de registro de cada orden guardada (sin huellas)."""
    t0 = time.time()
    filas = leer_por_clave("ordenes_servicio?select=id_os,fecha_registro")
    o = {int(f["id_os"]): [None, None, str(f.get("fecha_registro") or "")[:10]] for f in filas}
    print(f"Estado armado desde la base: {len(o)} órdenes en {time.time() - t0:.0f} s.", flush=True)
    return {"v": ESTADO_VERSION, "o": o, "t": {}, "arranque": True}


# ---------------------------------------------------------------- órdenes

def _reciente(f: dict, limite: str) -> bool:
    """¿La orden se tocó en los últimos días? (solo se usa en el arranque, cuando aún no hay huellas)."""
    for k in ("fecha_mod", "fecha_observacion", "atencion_fin", "atencion_inicio", "fecha_registro", "inicio_fachada"):
        v = f.get(k)
        if v and str(v)[:10] >= limite:
            return True
    return False


def upsert_ordenes(filas: list[dict], estado: dict, cajas_ok: bool) -> int:
    """Escribe las órdenes nuevas o que cambiaron y actualiza el estado. Devuelve cuántas se escribieron.

    cajas_ok: si los mapas de cajas se cargaron. Si no, se conservan las cajas guardadas (no se envía la columna)."""
    o = estado["o"]
    arranque = estado.get("arranque", False)
    limite = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    cambios = []
    for f in filas:
        h = huella(f)
        hc = (_hash(f["cajas_cerca"]) if f.get("cajas_cerca") else "") if cajas_ok else None
        previo = o.get(f["id_os"])
        fecha = str(f.get("fecha_registro") or "")[:10]
        if cajas_ok and previo is not None and previo[1] is None:
            previo[1] = hc  # cajas aún no conocidas (corridas sin mapas): se asumen las guardadas, sin reescribir
        if arranque and previo is not None and previo[0] is None:
            # Arranque: la orden ya está en la base. Se reescribe solo si se tocó hace poco; las demás se dan por iguales.
            if not _reciente(f, limite):
                o[f["id_os"]] = [h, hc, fecha]
                continue
        elif previo is not None and previo[0] == h and (not cajas_ok or previo[1] == hc):
            continue
        f["huella"] = h
        cambios.append((f, h, hc, fecha))

    print(f"Órdenes nuevas o con cambios: {len(cambios)} de {len(filas)}.", flush=True)
    total = 0
    for i in range(0, len(cambios), LOTE):
        parte = cambios[i : i + LOTE]
        lote = [{k: v for k, v in f.items() if cajas_ok or k != "cajas_cerca"} for f, *_ in parte]
        _pedir("POST", _url("ordenes_servicio") + "?on_conflict=id_os", f"Lote {i // LOTE + 1}", json=lote,
               headers=_cabeceras({"Prefer": "resolution=merge-duplicates,return=minimal"}))
        for f, h, hc, fecha in parte:
            previo = o.get(f["id_os"])
            o[f["id_os"]] = [h, hc if cajas_ok else (previo[1] if previo else None), fecha]
        total += len(parte)
    return total


def borrar_ausentes(ids_reporte: set[int], estado: dict, desde: str, reporte_completo: bool) -> int:
    """Borra de la base las órdenes que ya no están en el reporte.

    Siempre se borran las de antes del periodo (fecha < desde). Las del periodo que faltan en el reporte solo se
    borran si el reporte llegó completo (todas las filiales), para no perder datos por una descarga a medias."""
    o = estado["o"]
    borrar = [i for i, v in o.items() if i not in ids_reporte and ((v[2] or "") < desde or reporte_completo)]
    if not borrar:
        return 0
    for k in range(0, len(borrar), LOTE_BORRADO):
        parte = borrar[k : k + LOTE_BORRADO]
        _pedir("DELETE", _url("ordenes_servicio") + f"?id_os=in.({','.join(map(str, parte))})", "Borrado",
               headers=_cabeceras({"Prefer": "return=minimal"}))
        for i in parte:
            o.pop(i, None)
    return len(borrar)


# ---------------------------------------------------------------- tableros y registro

def guardar_tableros(tableros: dict[str, dict], estado: dict, actualizado: str) -> int:
    """Guarda solo los tableros cuyo contenido cambió. Devuelve cuántos se escribieron."""
    t = estado.setdefault("t", {})
    n = 0
    for clave, datos in tableros.items():
        h = _hash(datos)
        if t.get(clave) == h:
            continue
        _pedir("POST", _url("tableros") + "?on_conflict=clave", f"Tablero {clave}",
               json={"clave": clave, "datos": datos, "actualizado": actualizado},
               headers=_cabeceras({"Prefer": "resolution=merge-duplicates,return=minimal"}))
        t[clave] = h
        n += 1
    return n


def registrar_corrida(modo: str, filas: int | None, ok: bool, mensaje: str, inicio: str, fin: str) -> None:
    _pedir("POST", _url("sincronizaciones"), "Registro de corrida",
           json={"modo": modo, "filas": filas, "ok": ok, "mensaje": mensaje[:2000], "inicio": inicio, "fin": fin},
           headers=_cabeceras({"Prefer": "return=minimal"}))


def limpiar_diagnosticos() -> None:
    """Borra los registros de diagnóstico temporales (clave «diag:…») de la tabla tableros, si quedara alguno."""
    _pedir("DELETE", _url("tableros") + "?clave=like.diag:*", "Limpieza de diagnósticos", headers=_cabeceras({"Prefer": "return=minimal"}))

