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
  v = válida (llega al mínimo) · b = bajo el mínimo (la red no entregó) · n = no certificable (otra red que no es Mi Fibra,
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

    # Veces que aparece cada enlace en todo el periodo (también repetido dentro del mismo AB).
    veces: dict[str, int] = defaultdict(int)
    for ido in x["id_os"]:
        for clave, _, _ in links.get(int(ido), []):
            veces[clave] += 1

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
        validos = [l for l in ls if veces[l[0]] == 1]
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
            if veces[clave] > 1:
                rep[clave].append([len(filas) - 1, url])

    # Enlaces repetidos: [enlace, [índices de filas donde aparece]].
    repetidos = [[v[0][1], [i for i, _ in v]] for _, v in sorted(rep.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
    return {"corte": corte, **tablas, "o": filas, "rep": repetidos,
            "cols": ["filial", "fecha_atencion", "eecc", "tecnico", "plan", "ab", "os", "cliente", "tecnico_n", "sin_identificar", "repetidos", "plan_nombre", "pruebas"]}


# ---------- Velocidad de cada prueba (speedtest.net) ----------
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
       "Accept": "application/json"}


def _api(clave: str) -> str | None:
    """speedtest.net/result/123 · /result/d/uuid · /my-result/d/uuid → https://www.speedtest.net/api/result/…"""
    m = re.match(r"speedtest\.net/(?:my-)?(result/[a-z0-9/_-]+)$", clave)
    return f"https://www.speedtest.net/api/{m.group(1)}" if m else None


def _leer(clave: str):
    """[bajada Mbps, subida Mbps, red Mi Fibra 1/0, fecha epoch] · [-1] si el enlace no existe · None si no se pudo leer (se reintenta)."""
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
        return [round((j.get("download") or 0) / 1000), round((j.get("upload") or 0) / 1000), 1 if "fibra" in isp else 0, int(j.get("date") or 0)]
    except Exception:  # noqa: BLE001
        return None


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
    try:
        r = requests.post(url, json={"claves": lote}, headers={"x-robot-firma": firma}, timeout=30)
        d = r.json().get("r", {}) if r.status_code == 200 else {}
    except Exception:  # noqa: BLE001
        d = {}
    return [d.get(c) for c in lote]


def velocidades(tab: dict, cache: dict, limite: int = 800, segundos: int = 150) -> None:
    """Completa cada prueba del tablero con su velocidad: fila[12] = [[quién, bajada, subida, estado], …] (estado: v/b/n/p).
    Primero el mes en curso (del día más nuevo al más antiguo) y después los meses anteriores; lo ya leído queda en la
    caché (estado del robot + copia en Supabase) y no se vuelve a pedir."""
    filas = tab.get("o") or []
    mes = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-5))).strftime("%Y-%m")
    falta, vistos, mes_claves = [], set(), set()
    for f in sorted(filas, key=lambda x: (x[1][:7] == mes, x[1]), reverse=True):
        for clave, _ in (f[12] if len(f) > 12 else []):
            if clave not in cache and clave not in vistos:
                vistos.add(clave)
                falta.append(clave)
                if f[1][:7] == mes:
                    mes_claves.add(clave)
    leidos, t0, via = 0, time.time(), "directo"
    if falta:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for i in range(0, min(len(falta), limite), 40):
                if time.time() - t0 > segundos:
                    break
                lote = falta[i:i + 40]
                res = list(ex.map(_leer, lote)) if via == "directo" else _por_portal(lote)
                if via == "directo" and all(v is None for v in res):   # speedtest.net no responde a GitHub: se pasa al portal (Vercel)
                    via = "portal"
                    res = _por_portal(lote)
                for clave, v in zip(lote, res):
                    if v is not None:
                        cache[clave] = v
                        leidos += 1
        del_mes = sum(1 for c in falta if c not in cache and c in mes_claves)
        print(f"  Speed Test: {leidos} velocidades nuevas leídas de speedtest.net ({'desde el robot' if via == 'directo' else 'vía el portal'}) · "
              f"{max(0, len(falta) - leidos)} pendientes ({del_mes} del mes en curso).", flush=True)
    for f in filas:
        if len(f) <= 12:
            continue
        minimo = 1200 if f[4] == 1 else 600
        dia = datetime.strptime(f[1], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() + 5 * 3600
        out = []
        for clave, q in f[12]:
            v = cache.get(clave)
            if v is None:
                out.append([q, None, None, "p"])
            elif v[0] < 0:
                out.append([q, None, None, "n"])
            else:
                lejos = v[3] and abs(v[3] - dia) > 3 * 86400 + 86400
                e = "n" if (not v[2] or lejos) else ("v" if v[0] >= minimo else "b")
                out.append([q, v[0], v[1], e])
        f[12] = out
    tab["cols"][-1] = "pruebas"
