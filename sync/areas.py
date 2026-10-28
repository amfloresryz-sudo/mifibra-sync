"""Áreas de los planos (polígonos de Google My Maps) para el mapa de Ruteo de la pestaña Pendientes.

Los IDs de los mapas no van en el código (repositorio público): se guardan en Supabase, tabla ajustes, clave
"robot:areas_mapas" → {"mapas": ["ID", ...]}. El robot los descarga, simplifica cada polígono (~10 m) y deja el
resultado en el tablero "areas". Si algún mapa no se puede leer no se toca lo guardado.
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET

import requests

CLAVE_AJUSTE = "robot:areas_mapas"
TOL = 0.0001          # grados (~11 m): tolerancia de simplificación
_t = lambda el: el.tag.rsplit("}", 1)[-1]  # noqa: E731


def _mapas() -> list[str]:
    env = [m.strip() for m in os.environ.get("AREAS_MAPAS", "").split(",") if m.strip()]
    if env:
        return env
    try:
        url = os.environ["SUPABASE_URL"].rstrip("/") + f"/rest/v1/ajustes?select=datos&clave=eq.{CLAVE_AJUSTE}"
        k = os.environ["SUPABASE_SERVICE_KEY"]
        r = requests.get(url, headers={"apikey": k, "Authorization": f"Bearer {k}"}, timeout=60)
        r.raise_for_status()
        f = r.json()
        return [str(m) for m in (f[0]["datos"].get("mapas") or [])] if f else []
    except Exception as e:  # noqa: BLE001
        print(f"Aviso: no se leyó la lista de mapas de áreas: {e}", flush=True)
        return []


def _simplificar(p: list[tuple[float, float]], tol: float = TOL) -> list[tuple[float, float]]:
    """Douglas-Peucker iterativo. Un anillo cerrado se parte en el punto más lejano del inicio."""
    if len(p) < 4:
        return p
    if p[0] == p[-1]:
        k = max(range(len(p)), key=lambda i: (p[i][0] - p[0][0]) ** 2 + (p[i][1] - p[0][1]) ** 2)
        if 0 < k < len(p) - 1:
            return _simplificar(p[:k + 1], tol)[:-1] + _simplificar(p[k:], tol)
    keep = [False] * len(p)
    keep[0] = keep[-1] = True
    pila = [(0, len(p) - 1)]
    while pila:
        a, b = pila.pop()
        (y1, x1), (y2, x2) = p[a], p[b]
        dx, dy = x2 - x1, y2 - y1
        n = (dx * dx + dy * dy) ** 0.5 or 1e-12
        mx, im = 0.0, -1
        for i in range(a + 1, b):
            y, x = p[i]
            d = abs(dy * x - dx * y + x2 * y1 - y2 * x1) / n
            if d > mx:
                mx, im = d, i
        if mx > tol and im > 0:
            keep[im] = True
            pila += [(a, im), (im, b)]
    return [q for q, k in zip(p, keep) if k]


def _coords(txt: str) -> list[tuple[float, float]]:
    out = []
    for par in (txt or "").split():
        v = par.split(",")
        try:
            out.append((round(float(v[1]), 5), round(float(v[0]), 5)))   # (lat, lon)
        except (ValueError, IndexError):
            continue
    return out


def _parsear(texto: bytes) -> list[dict]:
    raiz = ET.fromstring(texto)
    doc = next((e for e in raiz.iter() if _t(e) == "Document"), raiz)
    out: list[dict] = []

    def rec(nodo, carpeta):
        for h in nodo:
            g = _t(h)
            if g == "Folder":
                rec(h, next((c.text.strip() for c in h if _t(c) == "name" and c.text), ""))
            elif g == "Document":
                rec(h, carpeta)
            elif g == "Placemark":
                nom = next((c.text.strip() for c in h if _t(c) == "name" and c.text), "")
                for pol in (e for e in h.iter() if _t(e) == "Polygon"):
                    ext = next((e for e in pol.iter() if _t(e) == "outerBoundaryIs"), pol)
                    co = next((e.text for e in ext.iter() if _t(e) == "coordinates" and e.text), "")
                    p = _simplificar(_coords(co))
                    if len(p) >= 3:
                        out.append({"n": nom, "c": carpeta, "t": "p", "p": p})
                for ln in (e for e in h.iter() if _t(e) == "LineString"):
                    co = next((e.text for e in ln.iter() if _t(e) == "coordinates" and e.text), "")
                    p = _simplificar(_coords(co))
                    if len(p) >= 2:
                        out.append({"n": nom, "c": carpeta, "t": "l", "p": p})

    rec(doc, "")
    return out


def cargar(corte: str, timeout: int = 90) -> dict | None:
    mapas = _mapas()
    if not mapas:
        return None
    figuras = []
    for mid in mapas:
        try:
            r = requests.get(f"https://www.google.com/maps/d/kml?mid={mid}&forcekml=1", timeout=timeout)
            r.raise_for_status()
            f = _parsear(r.content)
            figuras += f
            print(f"Mapa de áreas {mid}: {len(f)} figuras.", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"Aviso: no se pudo leer el mapa de áreas {mid}: {e}", flush=True)
            return None
    return {"areas": figuras, "corte": corte}
