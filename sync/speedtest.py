"""Tablero de Speed Test: pruebas de velocidad que los técnicos dejan en «Observaciones Tecnico» de cada alta.

Reglas (acordadas con operaciones):
- Solo altas de internet DESCARGADAS (INSTALACION SERVICIO, servicio INTERNET), por fecha de atención.
- AB sin enlace → «Sin medición».
- Cada enlace es del cliente o del técnico según la etiqueta que lo antecede; sin etiqueta → técnico.
  Cliente = la etiqueta dice test/prueba cliente (o algo del cliente); técnico = test velocidad técnico, técnico…
  Sin etiqueta (aunque sean 2 o más enlaces) → prueba del técnico.
- Un enlace que aparece más de una vez (en varios AB o repetido en el mismo) NO cuenta; se lista en detalle.

Se guarda compacto en la tabla `tableros`, clave "speedtest": tablas de nombres + una fila por alta.
"""
from __future__ import annotations

import re
from collections import defaultdict

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
        sid = 0   # regla de operaciones: enlaces sin etiqueta (aunque sean 2 o más) cuentan como prueba del técnico
        tec_n = len(validos) - cli - sid
        fi, ei, ki = ix("f", _txt(r.filial) or "SIN FILIAL"), ix("e", ec), ix("k", tec)
        dia = r.fa.strftime("%Y-%m-%d")
        ab = int(r.abonado_codigo) if pd.notna(r.abonado_codigo) else None
        os_ = int(r.nro_os) if pd.notna(r.nro_os) else None
        filas.append([fi, dia, ei, ki, plan_clase(r.tarifa_servicio), ab, os_, cli, tec_n, sid, n_rep, ix("pn", plan_nombre(r.tarifa_servicio))])
        for clave, url, _ in ls:
            if veces[clave] > 1:
                rep[clave].append([len(filas) - 1, url])

    # Enlaces repetidos: [enlace, [índices de filas donde aparece]].
    repetidos = [[v[0][1], [i for i, _ in v]] for _, v in sorted(rep.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
    return {"corte": corte, **tablas, "o": filas, "rep": repetidos,
            "cols": ["filial", "fecha_atencion", "eecc", "tecnico", "plan", "ab", "os", "cliente", "tecnico_n", "sin_identificar", "repetidos", "plan_nombre"]}
