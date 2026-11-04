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


def _canon(clave: str) -> str | None:
    """Forma que entiende la API de speedtest.net: speedtest.net/result/<número>.
    El número se busca en todo el enlace, venga como result/d/123, result/i/123, my-result/a/123, a/123, con «.png»
    o con texto pegado («123test», «123%7c», un segundo enlace detrás…). Antes result/d/123 daba «no existe».
    Los de código largo (my-result/d/<uuid>) son resultados PRIVADOS de la app de escritorio: speedtest.net responde
    «Result not found» sin iniciar sesión, así que no tienen velocidad pública → «uuid:…» (no se piden)."""
    c = str(clave).replace(".png", "")
    m = re.search(r"(?:result|a)/(?:[a-z]/)?(\d{7,12})", c)
    if m:
        return f"speedtest.net/result/{m.group(1)}"
    m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", c)
    if m:
        return f"uuid:{m.group(0)}"
    return None


def _api(clave: str) -> str | None:
    """Enlace de la prueba → https://www.speedtest.net/api/result/…"""
    c = _canon(clave)
    return f"https://www.speedtest.net/api/{c[len('speedtest.net/'):]}" if c and c.startswith("speedtest.net/") else None


def _leer(clave: str):
    """[bajada Mbps, subida Mbps, red Mi Fibra 1/0, fecha epoch] · [-1] si el enlace no existe · None si no se pudo leer (se reintenta)."""
    import requests
    url = _api(clave)
    if not url:
        return [-1, 2]
    try:
        r = requests.get(url, headers=_UA, timeout=8)
    except Exception:  # noqa: BLE001
        return None
    if r.status_code == 404:
        return [-1, 2]   # 2 = revisado con la lectura corregida (no se vuelve a pedir)
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
    canon = [_canon(c) for c in lote]   # al portal se le manda la forma que entiende (result/<número>)
    canon = [c if c and c.startswith("speedtest.net/") else None for c in canon]   # privados/rotos: no se piden
    pedir = sorted({c for c in canon if c})
    d = {}
    if pedir:
        try:
            r = requests.post(url, json={"claves": pedir}, headers={"x-robot-firma": firma}, timeout=20)
            d = r.json().get("r", {}) if r.status_code == 200 else {}
        except Exception:  # noqa: BLE001
            d = {}
    out = []
    for c in canon:
        v = d.get(c) if c else [-1]
        out.append([-1, 2] if (v is not None and v[0] < 0) else v)
    return out


def _referencia() -> tuple[dict, list]:
    """Archivos de control de Speed Test (sync/ref/speedtest_*.json):
    «o»: «FILIAL|N.º OS» → [[quién C/T, bajada, subida, estado v/b/n, N.º prueba]] (si una OS está aquí, manda);
    «p»: todas las pruebas del archivo [N.º prueba, quién C/T, orden] (instalaciones y averías), para aprender sus patrones.
    El quién sale de Ookla (más confiable que el título del enlace)."""
    import json
    from pathlib import Path
    ref, pruebas = {}, []
    for p in sorted((Path(__file__).parent / "ref").glob("speedtest_*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            ref.update(d.get("o") or {})
            pruebas += d.get("p") or []
        except Exception as e:  # noqa: BLE001
            print(f"Aviso: no se pudo leer {p.name}: {e}", flush=True)
    return ref, pruebas


def _grupo(clave: str) -> str:
    """Tipo de número de resultado de Speedtest: sus 2 primeras cifras y su largo (91/9 = app del técnico, 19/11 = web…)."""
    c = _canon(clave) if "speedtest.net" in str(clave) else str(clave)
    m = re.search(r"/(\d+)$", "/" + str(c or "").replace("speedtest.net/result/", ""))
    if not m:
        return "d"
    return f"{m.group(1)[:2]}/{len(m.group(1))}"


class _Patrones:
    """Aprende de los archivos de control quién hizo cada prueba (cliente C / técnico T) según:
    el tipo de número (_grupo), el título del enlace en el comentario y si la orden tiene una prueba de la app del técnico (91/9).
    Para una prueba nueva usa la combinación más parecida con al menos 5 casos; si no hay, el título del enlace."""
    MIN = 5

    def __init__(self, filas: list, fil: list, ref: dict, pruebas: list):
        from collections import Counter
        self.t = defaultdict(Counter)
        por_orden = defaultdict(list)
        for n, q, orden in pruebas:
            por_orden[orden].append((str(n), q))
        for ls in por_orden.values():   # 1) todas las pruebas del archivo (sin título)
            hay_a = any(_grupo(n) == "91/9" for n, _ in ls)
            for n, q in ls:
                self.t[("g", _grupo(n), hay_a)][q] += 1
                self.t[("g", _grupo(n))][q] += 1
        self.con_titulo = 0
        for f in filas:                 # 2) las mismas pruebas con el título que puso el técnico en el comentario
            k = f"{fil[f[0]] if f[0] < len(fil) else ''}|{f[6]}"
            if k not in ref or len(f) <= 12:
                continue
            quien = {str(p[4]): p[0] for p in ref[k] if len(p) > 4}
            hay_a = any(_grupo(c) == "91/9" for c, _ in f[12])
            for c, q in f[12]:
                m = re.search(r"/(\d+)$", _canon(c) or "")
                if m and m.group(1) in quien:
                    self.t[("t", _grupo(c), q, hay_a)][quien[m.group(1)]] += 1
                    self.t[("t", _grupo(c), q)][quien[m.group(1)]] += 1
                    self.con_titulo += 1

    def quien(self, clave: str, titulo: str, hay_a: bool) -> str:
        g = _grupo(clave)
        for k in (("t", g, titulo, hay_a), ("t", g, titulo), ("g", g, hay_a), ("g", g)):
            c = self.t.get(k)
            if c and sum(c.values()) >= self.MIN:
                return c.most_common(1)[0][0]
        return titulo or "T"


def velocidades(tab: dict, cache: dict, limite: int = 800, segundos: int = 150) -> None:
    """Completa cada prueba del tablero con su velocidad: fila[12] = [[quién, bajada, subida, estado], …] (estado: v/b/n/p).
    Primero el mes en curso (del día más nuevo al más antiguo) y después los meses anteriores; lo ya leído queda en la
    caché (estado del robot + copia en Supabase) y no se vuelve a pedir."""
    filas = tab.get("o") or []
    # Enlaces que antes se leyeron mal como «no existe» (result/d/123, my-result/a/123, ….png): se vuelven a pedir una vez.
    for k in [k for k, v in cache.items() if v and v[0] < 0 and len(v) == 1 and (_canon(k) or "").startswith("speedtest.net/")
              and _canon(k) != k]:
        del cache[k]
    ref, pruebas_ref = _referencia()
    fil = tab.get("f") or []
    en_ref = lambda f: f"{fil[f[0]] if f[0] < len(fil) else ''}|{f[6]}" in ref
    mes = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-5))).strftime("%Y-%m")
    falta, vistos, mes_claves = [], set(), set()
    for f in sorted(filas, key=lambda x: (x[1][:7] == mes, x[1]), reverse=True):
        if en_ref(f):   # ya viene en un archivo de control: no hace falta leerla en speedtest.net
            continue
        for clave, _ in (f[12] if len(f) > 12 else []):
            if clave not in cache and clave not in vistos:
                vistos.add(clave)
                falta.append(clave)
                if f[1][:7] == mes:
                    mes_claves.add(clave)
    leidos, fallas, t0, via = 0, 0, time.time(), "directo"
    if falta:
        objetivo = falta[:limite]

        def anotar(lote, res):
            nonlocal leidos, fallas
            for clave, v in zip(lote, res):
                if v is not None:
                    cache[clave] = v
                    leidos += 1
                else:
                    fallas += 1

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
              f"{fallas} sin respuesta (se reintentan) · {max(0, len(falta) - leidos)} pendientes ({del_mes} del mes en curso).", flush=True)
    n_ref = cambia = 0
    pat = _Patrones(filas, fil, ref, pruebas_ref) if pruebas_ref else None
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
        if pat:   # quién hizo cada prueba: patrones aprendidos de los archivos de control (Ookla), no solo el título
            hay_a = any(_grupo(c) == "91/9" for c, _ in f[12])
            nuevo = []
            for c, q in f[12]:
                q2 = pat.quien(c, q, hay_a)
                cambia += q2 != (q or "T")
                nuevo.append([c, q2])
            f[12] = nuevo
            f[7], f[8], f[9] = sum(1 for _, q in nuevo if q == "C"), sum(1 for _, q in nuevo if q != "C"), 0
        for clave, q in f[12]:
            v = cache.get(clave)
            if v is None:
                out.append([q, None, None, "p"])
            elif v[0] < 0:
                out.append([q, None, None, "n"])
            else:
                # Regla del control de provincias: la prueba vale solo si es de máximo 1 día respecto de la atención
                # (del día anterior al siguiente); si no, no suma en ningún cálculo.
                if v[3] and not (dia - 86400 <= v[3] < dia + 2 * 86400):
                    i = 7 if q == "C" else 9 if not q else 8
                    f[i] = max(0, f[i] - 1)
                    continue
                # Bajo el mínimo: en el equipo del cliente es «no certificable» (no reporta la velocidad de su enlace,
                # como en el control); en el del técnico, «la red no entregó».
                e = "n" if not v[2] else ("v" if v[0] >= minimo else ("n" if q == "C" else "b"))
                out.append([q, v[0], v[1], e])
        f[12] = out
    if ref:
        print(f"  Speed Test: {n_ref} instalaciones tomadas de los archivos de control (sync/ref).", flush=True)
    if pat:
        print(f"  Speed Test: quién hizo la prueba según los patrones de {len(pruebas_ref)} pruebas de control "
              f"({pat.con_titulo} con título); {cambia} pruebas cambiaron respecto del título del enlace.", flush=True)
    tab["cols"][-1] = "pruebas"
