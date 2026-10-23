"""Tablero del buscador FFTT (facilidades técnicas): altas de internet DESCARGADA del periodo del reporte, con su TAP (ZONA),
caja (NAP), borne, cintillo (Dispositivo), EECC, técnico y planta. La página valida el formato por planta y busca cintillos
duplicados; aquí solo se arma la lista (sin leer Supabase)."""
import pandas as pd

from . import instalaciones, procesar

COLS = ["fi", "di", "ab", "os", "fa", "cin", "zo", "nap", "bor", "ec", "tec", "pl", "seg"]


def _t(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def construir(df: pd.DataFrame, corte: str) -> dict:
    d = df[(df["tipo_orden"] == "INSTALACION SERVICIO") & (df["estado_os"] == "DESCARGADA")]
    plantas = instalaciones.CONFIG["plantas"]
    filas = []
    for r in d.itertuples(index=False):
        g = r._asdict()
        fa = _t(g.get("fecha_atencion"))[:10]
        if not fa:
            continue
        filas.append([_t(g.get("filial")), _t(g.get("distrito")), int(g["abonado_codigo"]), int(g["nro_os"]), fa,
                      _t(g.get("dispositivo")), _t(g.get("zona_tap")), _t(g.get("nap")), _t(g.get("borne")),
                      _t(g.get("contrata")), _t(g.get("tecnico_ejecutor")) or _t(g.get("tecnico_responsable")),
                      instalaciones._planta(g.get("plano"), plantas), _t(g.get("segmento"))])
    filas.sort(key=lambda f: (f[4], f[3]), reverse=True)
    return {"cols": COLS, "filas": filas, "corte": corte, "zona": procesar.ZONA_FFTT}
