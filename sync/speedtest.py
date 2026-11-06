"""Tablero de Speed Test: pruebas de velocidad que los técnicos dejan en «Observaciones Tecnico» de cada alta.

Reglas (acordadas con operaciones):
- Solo altas de internet DESCARGADAS (INSTALACION SERVICIO, servicio INTERNET), por fecha de atención.
- AB sin enlace → «Sin medición».
- Cada enlace es del cliente o del técnico según la etiqueta que lo antecede; sin etiqueta → técnico.
  Cliente = la etiqueta dice test/prueba cliente (o algo del cliente); técnico = test velocidad técnico, técnico…
  Sin etiqueta (aunque sean 2 o más enlaces) → prueba del técnico.
- Un enlace que aparece más de una vez (en varios AB o repetido en el mismo) NO cuenta; se lista en detalle.
- Velocidad (como el control de provincias): el robot lee cada enlace en speedtest.net una sola vez (caché en el estado).
  Mínimo del plan: 600 Mbps de bajada en planes de hasta 1.000 Mbps; 1.200 Mbps en planes mayores.
  v = válida (descarga mayor al mínimo) · b = bajo el mínimo (la red no entregó) · n = no certificable (otra red que no es Mi Fibra,
  o la prueba es de más de 3 días antes/después de la instalación, o el enlace no existe) · p = velocidad aún pendiente de leer.

Se guarda compacto en la tabla `tableros`, clave "speedtest": tablas de nombres + una fila por alta.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd

from .averias import _contrata, _txt

_RE_PLAN = re.compile(r"(\d{2,5})")


def plan_clase(tarifa) -> int:
    """0 = hasta 1.000 Mbps · 1 = más de 1.000 Mbps · 2 = sin plan identificado."""
    nums = [int(n) for n in _RE_PLAN.findall(_txt(tarifa)) if 50 <= int(n) <= 20000]
    if not nums:
        return 2
    return 0 if nums[0] <= 1000 else 1


def plan_nombre(tarifa) -> str:
    """Plan tal como se ve en la pestaña: «500 Mbps», «2000 Mbps»… (el número de la tarifa); sin número, la tarifa tal cual."""
    t = _txt(tarifa)
    nums = [int(n) for n in _RE_PLAN.findall(t) if 50 <= int(n) <= 20000]
    if nums:
        return f"{nums[0]} Mbps"
    return t.title() if t else "Sin plan"


def construir(df: pd.DataFrame, links: dict[int, list], tec_contrata: dict[str, str], corte: str) -> dict:
    """df: órdenes del periodo (columnas de la base). links: id_os → [[clave, enlace, quién]]."""
    x = df[(df["tipo_orden"] == "INSTALACION SERVICIO") & (df["estado_os"] == "DESCARGADA")].copy()
    if "servicio" in x and x["servicio"].notna().any():
        x = x[x["servicio"].fillna("INTERNET") == "INTERNET"]
    x["fa"] = pd.to_datetime(x["fecha_atencion"], errors="coerce")
    x = x[x["fa"].notna()].sort_values(["fa", "id_os"])

    # Veces que aparece cada PRUEBA (su N.º de resultado, escrita en cualquier forma de enlace: result/N, result/d/N, .png…)
    # en todo el periodo, también repetida dentro del mismo AB. Regla del control: un N.º citado en 2 o más órdenes se descarta de todas.
    rid = lambda c: _api(c) or c
    veces: dict[str, int] = defaultdict(int)
    for ido in x["id_os"]:
        for clave, _, _ in links.get(int(ido), []):
            veces[rid(clave)] += 1

    tablas = {"f": [], "e": [], "k": [], "pn": []}
    idx = {k: {} for k in tablas}

    def ix(t, v):
        if v not in idx[t]:
            idx[t][v] = len(tablas[t])
            tablas[t].append(v)
        return idx[t][v]

    filas, rep = [], defaultdict(list)
    for r in x.itertuples(index=False):
        ido = int(r.id_os)
        tec = _txt(r.tecnico_ejecutor) or "SIN TÉCNICO"
        ec = _contrata(r.contrata) or _contrata(tec_contrata.get(tec)) or "SIN EECC"
        ls = links.get(ido, [])
        # Quién hizo la prueba (regla de operaciones, 04/11/2026): manda el título que el técnico escribe antes del enlace;
        # sin título, el tipo de enlace: «my-result» = técnico, cualquier otro enlace = cliente.
        validos = [[l[0], l[1], l[2] or ("T" if "my-result" in str(l[0]) else "C")] for l in ls if veces[rid(l[0])] == 1]
        n_rep = len(ls) - len(validos)
        etiquetados = any(l[2] for l in validos)
        cli = sum(1 for l in validos if l[2] == "C")
        sid = sum(1 for l in validos if not l[2])   # sin etiqueta: la página los suma al técnico (regla de operaciones); aquí se cuentan aparte
        tec_n = len(validos) - cli - sid
        fi, ei, ki = ix("f", _txt(r.filial) or "SIN FILIAL"), ix("e", ec), ix("k", tec)
        dia = r.fa.strftime("%Y-%m-%d")
        ab = int(r.abonado_codigo) if pd.notna(r.abonado_codigo) else None
        os_ = int(r.nro_os) if pd.notna(r.nro_os) else None
        filas.append([fi, dia, ei, ki, plan_clase(r.tarifa_servicio), ab, os_, cli, tec_n, sid, n_rep, ix("pn", plan_nombre(r.tarifa_servicio)),
                      [[l[0], l[2] or ""] for l in validos]])   # [12] enlaces válidos; velocidades() los cambia por [quién, bajada, subida, estado]
        for clave, url, _ in ls:
            if veces[rid(clave)] > 1:
                rep[clave].append([len(filas) - 1, url])

    # Enlaces repetidos: [enlace, [índices de filas donde aparece]].
    repetidos = [[v[0][1], [i for i, _ in v]] for _, v in sorted(rep.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
    return {"corte": corte, **tablas, "o": filas, "rep": repetidos,
            "cols": ["filial", "fecha_atencion", "eecc", "tecnico", "plan", "ab", "os", "cliente", "tecnico_n", "sin_identificar", "repetidos", "plan_nombre", "pruebas"]}


# ---------- Velocidad de cada prueba (speedtest.net) ----------
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
       "Accept": "application/json"}


def _tipo_enlace(clave: str) -> str:
    """Tipo de enlace (1 letra): p = result/N (web) · d = result/d/N · a = app Android (my-result/a/N, a/N) · i = app iOS
    (result/i/N) · m = privado app de escritorio (my-result/d/código) · u = result/d/código · x = cortado o incompleto."""
    c = str(clave).replace(".png", "")
    if re.search(r"result/i/\d{6,}", c):
        return "i"
    if re.search(r"(?:my-result/a|result/a|net/a)/\d{6,}", c):
        return "a"
    if re.search(r"result/d/\d{6,}", c):
        return "d"
    if re.search(r"(?:my-)?result/\d{6,}", c):
        return "p"
    if re.search(r"my-result/d/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", c):
        return "m"
    if re.search(r"result/d/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", c):
        return "u"
    return "x"


SIN_PUBLICO = {"x"}   # enlace cortado o incompleto: no se puede pedir (my-result/d/código y result/d/código sí se leen)


def _api(clave: str) -> str | None:
    """Enlace con número (result/123, result/d/123, result/i/123, my-result/a/123, ….png) → https://www.speedtest.net/api/result/123.
    En el navegador esos enlaces sí muestran la prueba; antes result/d/123 se pedía mal y quedaba como «no encontrado»."""
    c = str(clave).replace(".png", "")
    m = re.search(r"(?:result|a)/(?:[a-z]/)?(\d{6,})", c)
    if m:
        return f"https://www.speedtest.net/api/result/{m.group(1)}"
    # App de escritorio con código (my-result/d/… y result/d/…): speedtest.net sí los muestra en /api/result/d/código
    u = re.search(r"result/d/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", c)
    return f"https://www.speedtest.net/api/result/d/{u.group(1)}" if u else None


def _leer(clave: str):
    """[bajada Mbps, subida Mbps, red Mi Fibra 1/0, fecha epoch, conexión, modo, ping, ping reposo, ping bajando, ping subiendo,
    servidor, patrocinador] (todo lo público de la prueba; conexión w=Wi-Fi e=cable c=celular, modo m=multi s=una) ·
    [-1] si el enlace no existe · None si no se pudo leer (se reintenta)."""
    import requests
    url = _api(clave)
    if not url:
        return [-1]
    try:
        r = requests.get(url, headers=_UA, timeout=8)
    except Exception:  # noqa: BLE001
        return None
    if r.status_code == 404:
        return [-1]
    if r.status_code != 200:
        return None
    try:
        j = r.json()
        isp = f"{j.get('isp_name') or ''} {j.get('display_provider_name') or ''}".lower()
        return _todo(j)
    except Exception:  # noqa: BLE001
        return None


def _num(x):
    try:
        return round(float(x), 1)
    except (TypeError, ValueError):
        return None


def _todo(j: dict) -> list:
    """Todos los datos públicos de una prueba de speedtest.net (los mismos que muestra su página)."""
    isp = f"{j.get('isp_name') or ''} {j.get('display_provider_name') or ''}".lower()
    icono = {"wireless": "w", "ethernet": "e", "cell": "c", "cellular": "c"}.get(str(j.get("connection_icon") or "").lower(), str(j.get("connection_icon") or "")[:8])
    modo = {"multi": "m", "single": "s"}.get(str(j.get("connection_mode") or "").lower(), "")
    return [round((j.get("download") or 0) / 1000), round((j.get("upload") or 0) / 1000), 1 if "fibra" in isp else 0, int(j.get("date") or 0),
            icono, modo, _num(j.get("latency")), _num(j.get("idle_latency")), _num(j.get("download_latency")), _num(j.get("upload_latency")),
            str(j.get("server_name") or "")[:40], str(j.get("sponsor_name") or "")[:40]]


def _por_portal(lote: list) -> list:
    """Respaldo: pide las velocidades a la función del portal en Vercel (otra red), firmada con la clave de Supabase."""
    import hashlib
    import hmac
    import os
    import requests
    k = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not k:
        return [None] * len(lote)
    dia = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    firma = hmac.new(k.encode(), f"speedtest-vel:{dia}".encode(), hashlib.sha256).hexdigest()
    url = os.environ.get("PORTAL_URL", "https://mifibra-sync.vercel.app").rstrip("/") + "/api/speedtest?accion=vel"
    canon = {c: ("speedtest.net/" + _api(c).split("/api/", 1)[1] if _api(c) else None) for c in lote}   # forma que entiende el portal
    pedir = sorted({v for v in canon.values() if v})
    try:
        r = requests.post(url, json={"claves": pedir}, headers={"x-robot-firma": firma}, timeout=20) if pedir else None
        d = r.json().get("r", {}) if r is not None and r.status_code == 200 else {}
    except Exception:  # noqa: BLE001
        d = {}
    return [d.get(canon[c]) if canon[c] else [-1] for c in lote]


def _referencia() -> dict:
    """Archivos de control de Speed Test (sync/ref/speedtest_*.json): «FILIAL|N.º OS» → [[quién C/T, bajada, subida, estado v/b/n]].
    Vienen del control operativo (el quién sale de Ookla, más confiable que el título del enlace); si una OS está aquí, manda."""
    import json
    from pathlib import Path
    ref = {}
    for p in sorted((Path(__file__).parent / "ref").glob("speedtest_*.json")):
        try:
            ref.update(json.loads(p.read_text(encoding="utf-8")).get("o") or {})
        except Exception as e:  # noqa: BLE001
            print(f"Aviso: no se pudo leer {p.name}: {e}", flush=True)
    return ref


def velocidades(tab: dict, cache: dict, limite: int = 800, segundos: int = 150) -> None:
    """Completa cada prueba del tablero con su velocidad: fila[12] = [[quién, bajada, subida, estado], …] (estado: v/b/n/p).
    Primero el mes en curso (del día más nuevo al más antiguo) y después los meses anteriores; lo ya leído queda en la
    caché (estado del robot + copia en Supabase) y no se vuelve a pedir."""
    filas = tab.get("o") or []
    # Enlaces que antes se dieron por «no existe» sin pedirlos bien (result/d/123, my-result/a/123, ….png, my-result/d/código):
    # se piden otra vez, una sola vez (lo que speedtest.net confirma que no existe queda como [-2] y ya no se repite).
    for k in [k for k, v in cache.items() if v == [-1] and _api(k) and not re.search(r"result/\d{6,}$", k)]:
        del cache[k]
    # Copia de los enlaces de cada instalación (para revisar tipos de enlace por día y filial; el portal no la lee).
    fil0 = tab.get("f") or []
    enlaces = {}
    for f in filas:
        if len(f) > 12 and f[12]:
            enlaces[f"{fil0[f[0]] if f[0] < len(fil0) else ''}|{f[6]}"] = [f[1], [[c, q] for c, q in f[12]]]
    tab["_enlaces"] = enlaces
    ref = _referencia()
    fil = tab.get("f") or []
    en_ref = lambda f: f"{fil[f[0]] if f[0] < len(fil) else ''}|{f[6]}" in ref
    mes = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-5))).strftime("%Y-%m")
    falta, vistos, mes_claves = [], set(), set()
    completar = []   # ya leídas antes solo con velocidad: se vuelven a pedir una vez para guardar todos sus datos públicos
    for f in sorted(filas, key=lambda x: (x[1][:7] == mes, x[1]), reverse=True):
        for clave, _ in (f[12] if len(f) > 12 else []):
            v = cache.get(clave)
            if v and v[0] >= 0 and len(v) == 4 and clave not in vistos:
                vistos.add(clave)
                completar.append(clave)
        if en_ref(f):   # ya viene en un archivo de control: no hace falta leerla en speedtest.net
            continue
        for clave, _ in (f[12] if len(f) > 12 else []):
            if clave not in cache and clave not in vistos and _tipo_enlace(clave) not in SIN_PUBLICO:
                vistos.add(clave)
                falta.append(clave)
                if f[1][:7] == mes:
                    mes_claves.add(clave)
    leidos, fallas, completadas, t0, via = 0, 0, 0, time.time(), "directo"
    if falta or completar:
        objetivo = falta[:limite]
        objetivo += completar[:max(0, limite - len(objetivo))]   # primero lo nuevo; con lo que sobra, se completan las ya leídas
        nuevas = set(falta)

        def anotar(lote, res):
            nonlocal leidos, fallas, completadas
            for clave, v in zip(lote, res):
                if v is None:
                    fallas += 1
                elif clave in nuevas:
                    cache[clave] = [-2] if v[0] < 0 else v   # [-2] = confirmado que no existe (no se vuelve a pedir)
                    leidos += 1
                elif v[0] >= 0:   # ya leída: solo se reemplaza si trae los datos completos (nunca se pierde la velocidad)
                    cache[clave] = v
                    completadas += 1

        with ThreadPoolExecutor(max_workers=8) as ex:
            i = 0
            while i < len(objetivo) and time.time() - t0 <= segundos:
                if via == "directo":
                    lote = objetivo[i:i + 40]
                    res = list(ex.map(_leer, lote))
                    if all(v is None for v in res):   # speedtest.net no responde a GitHub: se pasa al portal (Vercel)
                        via = "portal"
                        continue                      # el mismo lote se pide al portal
                    anotar(lote, res)
                    i += 40
                else:
                    # Portal: pedidos chicos (8 enlaces = una sola vuelta en Vercel, ~2–7 s) y 6 pedidos a la vez.
                    # Los pedidos grandes (40) pasaban el tiempo máximo de la función y volvían vacíos.
                    lotes = [objetivo[j:j + 8] for j in range(i, min(i + 48, len(objetivo)), 8)]
                    for lote, res in zip(lotes, ex.map(_por_portal, lotes)):
                        anotar(lote, res)
                    i += 48
        del_mes = sum(1 for c in falta if c not in cache and c in mes_claves)
        print(f"  Speed Test: {leidos} velocidades nuevas leídas de speedtest.net ({'desde el robot' if via == 'directo' else 'vía el portal'}) · "
              f"{fallas} sin respuesta (se reintentan) · {max(0, len(falta) - leidos)} pendientes ({del_mes} del mes en curso) · "
              f"{completadas} ya leídas completadas con todos sus datos ({max(0, len(completar) - completadas)} por completar).", flush=True)
    n_ref = 0
    for f in filas:
        if len(f) <= 12:
            continue
        clave_ref = f"{fil[f[0]] if f[0] < len(fil) else ''}|{f[6]}"
        if clave_ref in ref:   # el archivo de control manda: sus pruebas, quién la hizo (dato de Ookla) y su resultado
            P = ref[clave_ref]
            f[7], f[8], f[9] = sum(1 for p in P if p[0] == "C"), sum(1 for p in P if p[0] != "C"), 0
            f[12] = [list(p) for p in P]
            n_ref += 1
            continue
        minimo = 1200 if f[4] == 1 else 600
        dia = datetime.strptime(f[1], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() + 5 * 3600   # 00:00 de Lima
        out = []
        for clave, q in f[12]:
            v, t = cache.get(clave), _tipo_enlace(clave)   # [4] = tipo de enlace (para comparar cliente vs técnico)
            if t in SIN_PUBLICO or (v is not None and v[0] < 0):
                out.append([q, None, None, "n", t])   # «Resultado no encontrado» en speedtest.net
            elif v is None:
                out.append([q, None, None, "p", t])
            else:
                # Regla del control de provincias: la prueba vale solo si es de máximo 1 día respecto de la atención
                # (del día anterior al siguiente); si no, no suma en ningún cálculo.
                if v[3] and not (dia - 86400 <= v[3] < dia + 2 * 86400):
                    i = 7 if q == "C" else 9 if not q else 8
                    f[i] = max(0, f[i] - 1)
                    continue
                # Válida = descarga MAYOR al mínimo del plan (regla del control: exactamente 600,0 o 1.200,0 no llega)
                e = "n" if not v[2] else ("v" if v[0] > minimo else "b")
                out.append([q, v[0], v[1], e, t])
        f[12] = out
    if ref:
        print(f"  Speed Test: {n_ref} instalaciones tomadas de los archivos de control (sync/ref).", flush=True)
    tab["cols"][-1] = "pruebas"
