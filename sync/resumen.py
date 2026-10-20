"""Indicadores de la portada y del Dashboard del portal, calculados en memoria con el reporte recién descargado.

Antes los calculaba Supabase en cada visita (vistas v_ordenes_dia, v_tipo_estado, v_ranking_tecnicos y la función
f_indicadores) leyendo cientos de miles de órdenes; eso agotaba el disco del plan gratuito. Ahora el robot los calcula
una vez por corrida y los guarda ya listos en la tabla `tableros` (clave "indicadores:<FILIAL>"), y /api/indicadores
solo lee esa fila. La lógica es la misma que tenían las vistas.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import pandas as pd

INST = "INSTALACION SERVICIO"
ST = "SERVICIO TECNICO"
TZ = "America/Lima"


def _redondeo(x, dec: int = 1):
    """Redondeo como PostgreSQL (mitades hacia arriba), no el de Python (mitades al par)."""
    if x is None or pd.isna(x):
        return None
    return float(Decimal(str(x)).quantize(Decimal(1).scaleb(-dec), rounding=ROUND_HALF_UP))


def _top(serie: pd.Series) -> tuple[str | None, int]:
    """Valor más repetido (en empate, el primero en orden alfabético) y cuántas veces aparece."""
    s = serie.dropna()
    if s.empty:
        return None, 0
    c = s.value_counts()
    maximo = int(c.max())
    return sorted(c[c == maximo].index)[0], maximo


def _moda(serie: pd.Series):
    """Como mode() de PostgreSQL: el valor más frecuente; en empate, el menor."""
    s = serie.dropna()
    if s.empty:
        return None
    c = s.value_counts()
    return sorted(c[c == c.max()].index)[0]


def _iso_utc(serie_ts: pd.Series, fn) -> str | None:
    v = fn(serie_ts)
    return None if pd.isna(v) else v.tz_convert("UTC").isoformat()


def indicadores(df: pd.DataFrame, actualizado_en: str) -> dict:
    """df: órdenes de UNA filial del periodo (mes anterior + mes en curso), con las columnas de la base."""
    reg = pd.to_datetime(df["fecha_registro"], utc=True, errors="coerce")
    fin = pd.to_datetime(df["atencion_fin"], utc=True, errors="coerce")
    tipo, est = df["tipo_orden"], df["estado_os"]
    es_i, es_s = tipo == INST, tipo == ST

    st_ok = es_s & (est == "DESCARGADA") & fin.notna() & (fin >= reg)
    horas = ((fin - reg).dt.total_seconds() / 3600)[st_ok]

    obs = df[est == "OBSERVADA"]
    anu = df[est == "ANULADA"]
    sub_top, sub_n = _top(obs["submotivo_observacion"])
    anu_top, anu_n = _top(anu["motivo_anulacion"])

    inst_total = int(es_i.sum())
    inst_desc = int((es_i & (est == "DESCARGADA")).sum())
    ind = {
        "periodo_desde": _iso_utc(reg, lambda s: s.min()),
        "periodo_hasta": _iso_utc(reg, lambda s: s.max()),
        "total_ordenes": int(len(df)),
        "inst_total": inst_total,
        "inst_registradas": int((es_i & (est == "REGISTRADA")).sum()),
        "inst_descargadas": inst_desc,
        "inst_observadas": int((es_i & (est == "OBSERVADA")).sum()),
        "inst_anuladas": int((es_i & (est == "ANULADA")).sum()),
        "inst_efectividad_pct": _redondeo(100.0 * inst_desc / inst_total) if inst_total else None,
        "st_total": int(es_s.sum()),
        "st_registradas": int((es_s & (est == "REGISTRADA")).sum()),
        "st_descargadas": int((es_s & (est == "DESCARGADA")).sum()),
        "st_anuladas": int((es_s & (est == "ANULADA")).sum()),
        "st_horas_mediana": _redondeo(horas.median()) if len(horas) else None,
        "st_horas_promedio": _redondeo(horas.mean()) if len(horas) else None,
        "obs_total": int(len(obs)),
        "obs_comercial": int((obs["motivo_observacion"] == "COMERCIAL").sum()),
        "obs_tecnico": int((obs["motivo_observacion"] == "TÉCNICO").sum()),
        "obs_submotivo_top": sub_top,
        "obs_submotivo_top_n": sub_n,
        "anul_total": int(len(anu)),
        "anul_motivo_top": anu_top,
        "anul_motivo_top_n": anu_n,
        "actualizado_en": actualizado_en,
    }

    dia = reg.dt.tz_convert(TZ).dt.strftime("%Y-%m-%d")
    por_dia = (
        pd.DataFrame({"dia": dia, "i": es_i, "s": es_s})
        .dropna(subset=["dia"])
        .groupby("dia")[["i", "s"]].sum()
        .sort_index()
    )
    por_dia = [{"dia": d, "instalaciones": int(r.i), "servicio_tecnico": int(r.s)} for d, r in por_dia.iterrows()]

    por_estado = {str(k): int(v) for k, v in est.dropna().value_counts().items()}

    ok = (est == "DESCARGADA") & df["tecnico_ejecutor"].notna()
    rk = df[ok].assign(i=es_i[ok], s=es_s[ok])
    ranking = []
    if not rk.empty:
        g = rk.groupby("tecnico_ejecutor").agg(contrata=("contrata", _moda), instalaciones=("i", "sum"), servicio_tecnico=("s", "sum"))
        g["total"] = g["instalaciones"] + g["servicio_tecnico"]
        g = g.reset_index().sort_values(["instalaciones", "total", "tecnico_ejecutor"], ascending=[False, False, True]).head(10)
        ranking = [
            {"tecnico_ejecutor": r.tecnico_ejecutor, "contrata": r.contrata, "instalaciones": int(r.instalaciones),
             "servicio_tecnico": int(r.servicio_tecnico), "total": int(r.total)}
            for r in g.itertuples()
        ]

    return {"indicadores": ind, "por_dia": por_dia, "por_estado": por_estado, "ranking_tecnicos": ranking}


def tecnico_contrata(df: pd.DataFrame, hoy: pd.Timestamp) -> dict[str, str]:
    """Contrata habitual de cada técnico de EECC (antes la vista v_tecnico_contrata): la más frecuente en sus
    órdenes DESCARGADAS de los últimos 60 días."""
    reg = pd.to_datetime(df["fecha_registro"], utc=True, errors="coerce")
    limite = pd.Timestamp(hoy).tz_localize(TZ) - pd.Timedelta(days=60) if pd.Timestamp(hoy).tzinfo is None else pd.Timestamp(hoy) - pd.Timedelta(days=60)
    x = df[
        df["contrata"].astype(str).str.startswith("EECC")
        & df["tecnico_ejecutor"].notna()
        & (df["estado_os"] == "DESCARGADA")
        & (reg >= limite)
    ]
    if x.empty:
        return {}
    return {t: _moda(g["contrata"]) for t, g in x.groupby("tecnico_ejecutor")}
