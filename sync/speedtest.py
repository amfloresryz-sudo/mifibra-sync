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
  Reglas del Excel del Control Operativo: v = sobre el mínimo (descarga mayor al mínimo) · n = bajo el mínimo, no certificable
  (sin dato de enlace) · p = velocidad aún pendiente de leer. Un enlace que no abre o una prueba de otra red (no Mi Fibra) no es prueba.

Se guarda compacto en la tabla `tableros`, clave "speedtest": tablas de nombres + una fila por alta.
"""
from __future__ import annotations

import math
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
                      [[l[0], l[2] or "", next((x[2] or "" for x in ls if x[0] == l[0]), "")] for l in validos]])   # [12] enlaces válidos [clave, quién, título original]; velocidades() los cambia por [quién, bajada, subida, estado]
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
    # Envío automático diario del Control Operativo (lo guarda /api/speedtest?accion=control): manda sobre los archivos
    try:
        from . import subir
        r = subir._pedir("GET", subir._url("ajustes") + "?select=datos&clave=eq.speedtest:control", "Control Operativo (envío diario)",
                         headers=subir._cabeceras())
        d = (r.json() or [{}])[0].get("datos") or {}
        if d.get("o"):
            ref.update(d["o"])
            print(f"  Speed Test: envío del Control Operativo recibido {d.get('recibido', '')[:16]} ({len(d['o'])} instalaciones).", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"Aviso: no se pudo leer el envío del Control Operativo: {e}", flush=True)
    return ref


# ---------- Estimación para los días que el Control Operativo (Ookla) aún no envía ----------
# Quién hizo cada prueba (cliente/técnico) solo lo sabe Ookla. Para los días sin su archivo se ESTIMA con lo que Ookla
# resolvió los últimos 30 días en enlaces parecidos (tipo de enlace, título, si el AB trae además una app, historial del
# técnico) y se calibra por EECC con los últimos 5 días. Prueba del 4 al 11/11: error del día (todas las filiales) ~4 pp.
_lg = lambda p: math.log(min(1 - 1e-4, max(1e-4, p)) / (1 - min(1 - 1e-4, max(1e-4, p))))
_ex = lambda x: 1 / (1 + math.exp(-x))


def _num_prueba(clave: str) -> str | None:
    m = re.findall(r"(\d{8,13})", clave or "")
    return m[-1] if m else None


def _grupo(clave: str, tit: str, enl: list) -> tuple:
    t = _tipo_enlace(clave)
    app = any(_tipo_enlace(x[0]) in ("d", "a", "m", "u") for x in enl if x[0] != clave)
    return (t, tit or "", app if t == "p" else None)


def _modelo_equipo(filas, fil, tecs, ecs, ref, cache) -> dict | None:
    dias_ref = sorted({f[1] for f in filas if len(f) > 12 and f"{fil[f[0]] if f[0] < len(fil) else ''}|{f[6]}" in ref})
    if not dias_ref:
        return None
    hasta = dias_ref[-1]
    desde = (datetime.strptime(hasta, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
    G, PT, ok = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0]), [0, 0]
    entren = []
    for f in filas:
        if len(f) <= 12 or not (desde <= f[1] <= hasta):
            continue
        P = ref.get(f"{fil[f[0]] if f[0] < len(fil) else ''}|{f[6]}")
        if P is None:
            continue
        por_id = {str(p[5]): p for p in P if len(p) > 5}
        entren.append((f, P))
        for x in f[12]:
            p = por_id.get(_num_prueba(x[0]) or "-")
            if not p:
                continue
            y = 1 if p[0] == "C" else 0
            g = _grupo(x[0], x[2] if len(x) > 2 else x[1], f[12])
            G[g][0] += y; G[g][1] += 1; ok[0] += y; ok[1] += 1
            if g[2] is not None:   # enlaces de navegador: historial del técnico
                PT[f[3]][0] += y; PT[f[3]][1] += 1
    if ok[1] < 200:
        return None
    m = {"hasta": hasta, "G": G, "PT": PT, "glob": ok[0] / ok[1],
         "gp": sum(v[0] for v in PT.values()) / max(1, sum(v[1] for v in PT.values())), "k": {}}
    # Calibración por filial+EECC: válidas reales (Ookla) ÷ esperadas por el modelo, en los últimos 7 días con datos
    d7 = (datetime.strptime(hasta, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y-%m-%d")   # últimos 5 días: sigue la tendencia
    K, KF = defaultdict(lambda: [0.0, 0.0]), defaultdict(lambda: [0.0, 0.0])
    for f, P in entren:
        if f[1] <= d7:
            continue
        minimo = 1200 if f[4] == 1 else 600
        real = any(p[0] == "C" and p[3] == "v" for p in P)
        pv = 1.0
        for x in f[12]:
            v = cache.get(x[0])
            p = next((p for p in P if len(p) > 5 and str(p[5]) == _num_prueba(x[0])), None)
            vel = p[1] if p else (v[0] if v and v[0] >= 0 else None)
            if vel is not None and vel > minimo:
                pv *= 1 - _p_cliente(m, x[0], x[2] if len(x) > 2 else x[1], f[12], f)
        K[(f[0], f[2])][0] += real; K[(f[0], f[2])][1] += 1 - pv
        KF[f[0]][0] += real; KF[f[0]][1] += 1 - pv
    m["kf"] = {fi: (r + 5) / (e + 5) for fi, (r, e) in KF.items()}   # por filial (la EECC chica se acerca a la de su filial)
    m["k"] = {g: (r + 5 * m["kf"].get(g[0], 1.0)) / (e + 5) for g, (r, e) in K.items()}
    return m


def _p_cliente(m, clave, tit, enl, f) -> float:
    if "my-result" in clave and not _num_prueba(clave):
        return 0.01   # app de escritorio del técnico (my-result/d/…): Ookla la da como técnico en el 99 % de los casos
    g = _grupo(clave, tit, enl)
    s, n = m["G"].get(g, [0, 0])
    base = (s + 1) / (n + 2) if n else m["glob"]
    if g[2] is None:
        return base
    s, n = m["PT"].get(f[3], [0, 0])
    pr = (s + 4 * m["gp"]) / (n + 4)
    return _ex(_lg(base) + 0.5 * (_lg(pr) - _lg(m["gp"])))


def _repartir_estimado(estim, m, fil, ecs) -> None:
    """Por filial+EECC+día: válidas esperadas = Σ P(válida) × calibración; se marcan como válidas (una prueba del cliente)
    las instalaciones con mayor probabilidad hasta completar ese número. Así los totales cuadran con lo esperado."""
    grupos = defaultdict(list)
    for e in estim:
        grupos[(e[0][0], e[0][2], e[0][1])].append(e)
    n_est = 0
    for (fi, ei, _), L in grupos.items():
        cuota = round(min(len(L), sum(e[2] for e in L) * m["k"].get((fi, ei), m.get("kf", {}).get(fi, 1.0))))
        L.sort(key=lambda e: -e[2])
        for k, (f, ps, pval) in enumerate(L):
            out = f[12]
            for p in out:
                p[0] = "T"
            if k < cuota and ps:
                validas = [(p, i) for i, p in ps if out[i][3] == "v"]
                if validas:
                    out[max(validas)[1]][0] = "C"
            f[7] = sum(1 for p in out if p[0] == "C"); f[8] = len(out) - f[7]; f[9] = 0
            while len(f) < 14:
                f.append(0)
            f[13] = 1
            n_est += 1
    print(f"  Speed Test: {n_est} instalaciones de días sin datos de Ookla (después del {m['hasta']}) con quién hizo la prueba ESTIMADO.", flush=True)


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
            enlaces[f"{fil0[f[0]] if f[0] < len(fil0) else ''}|{f[6]}"] = [f[1], [[x[0], x[1]] for x in f[12]]]
    tab["_enlaces"] = enlaces
    ref = _referencia()
    fil = tab.get("f") or []
    en_ref = lambda f: f"{fil[f[0]] if f[0] < len(fil) else ''}|{f[6]}" in ref
    mes = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-5))).strftime("%Y-%m")
    falta, vistos, mes_claves = [], set(), set()
    completar = []   # ya leídas antes solo con velocidad: se vuelven a pedir una vez para guardar todos sus datos públicos
    for f in sorted(filas, key=lambda x: (x[1][:7] == mes, x[1]), reverse=True):
        for clave, *_ in (f[12] if len(f) > 12 else []):
            v = cache.get(clave)
            if v and v[0] >= 0 and len(v) == 4 and clave not in vistos:
                vistos.add(clave)
                completar.append(clave)
        if en_ref(f):   # ya viene en un archivo de control: no hace falta leerla en speedtest.net
            continue
        for clave, *_ in (f[12] if len(f) > 12 else []):
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
    mod = _modelo_equipo(filas, fil, tab.get("k") or [], tab.get("e") or [], ref, cache)
    estim = []   # filas de días sin datos de Ookla: [fila, [prob. de cada prueba], P(válida)]
    for f in filas:
        if len(f) <= 12:
            continue
        clave_ref = f"{fil[f[0]] if f[0] < len(fil) else ''}|{f[6]}"
        enl = f[12]
        if clave_ref in ref:   # el archivo de control manda: sus pruebas, quién la hizo (dato de Ookla) y su resultado
            P = ref[clave_ref]
            f[7], f[8], f[9] = sum(1 for p in P if p[0] == "C"), sum(1 for p in P if p[0] != "C"), 0
            f[12] = [list(p) for p in P]
            n_ref += 1
            continue
        minimo = 1200 if f[4] == 1 else 600
        dia = datetime.strptime(f[1], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() + 5 * 3600   # 00:00 de Lima
        out = []
        probs = []
        for clave, q, *tit in f[12]:
            v, t = cache.get(clave), _tipo_enlace(clave)   # [4] = tipo de enlace (para comparar cliente vs técnico)
            # Regla del Excel del Control Operativo (base de Ookla): solo existen pruebas con velocidad hechas en la red Mi Fibra.
            # Un enlace que no abre o una prueba de otra red NO es prueba (no suma en ningún cálculo).
            if t in SIN_PUBLICO or (v is not None and (v[0] < 0 or (len(v) > 2 and not v[2]))):
                i = 7 if q == "C" else 9 if not q else 8
                f[i] = max(0, f[i] - 1)
                continue
            elif v is None:
                out.append([q, None, None, "p", t])
            else:
                # Regla del control de provincias: la prueba vale solo si es de máximo 1 día respecto de la atención
                # (del día anterior al siguiente); si no, no suma en ningún cálculo.
                if v[3] and not (dia - 86400 <= v[3] < dia + 2 * 86400):
                    i = 7 if q == "C" else 9 if not q else 8
                    f[i] = max(0, f[i] - 1)
                    continue
                # Regla del Excel: «Sobre el mínimo» = descarga MAYOR al mínimo del plan (600 / 1.200 Mbps; el exacto no llega).
                # Bajo el mínimo: speedtest.net no da el dato de enlace del equipo → «no certificable (sin dato de enlace)».
                e = "v" if v[0] > minimo else "n"
                out.append([q, v[0], v[1], e, t])
                probs.append((len(out) - 1, clave, tit[0] if tit else q))
        f[12] = out
        if mod and f[1] > mod["hasta"]:   # día que el Control Operativo aún no envía: se estima quién hizo cada prueba
            ps = [(i, _p_cliente(mod, clave, ti, enl, f)) for i, clave, ti in probs]
            pv = 1.0
            for i, p in ps:
                if out[i][3] == "v":
                    pv *= 1 - p
            estim.append([f, ps, 1 - pv])
    if estim:
        _repartir_estimado(estim, mod, fil, tab.get("e") or [])
    if ref:
        print(f"  Speed Test: {n_ref} instalaciones tomadas de los archivos de control (sync/ref).", flush=True)
    tab["cols"][-1] = "pruebas"
    if "estimado" not in tab["cols"]:
        tab["cols"].append("estimado")   # [13] = 1: quién hizo las pruebas es ESTIMADO (día que el Control Operativo aún no envía)
