"""Borra de Supabase las suspensiones / reconexiones / cortes de OFICINA ya descargados (sin EECC).

Desde el 11-nov-2026 el robot ya no las sube (eran ~80 % de ordenes_servicio y el portal no las lee de la base); esta
limpieza mensual quita las que se hayan colado (p. ej. una que se guardó REGISTRADA y luego la oficina la descargó).
Borra en tandas chicas con pausas para no agotar el disco del plan gratuito. Solo usa la API REST (SUPABASE_URL y
SUPABASE_SERVICE_KEY), así corre en GitHub Actions sin la computadora del usuario.

Uso: python -m sync.limpiar_oficina [--probar]   (--probar: solo cuenta, no borra)
"""
from __future__ import annotations

import sys
import time

from . import subir

FILTRO = ("tipo_orden=in.(%22SUSPENSION%20DE%20SERVICIO%22,%22RECONEXION%20SERVICIO%22,%22CORTE%20DEFINITIVO%22)"
          "&estado_os=eq.DESCARGADA&or=(contrata.is.null,contrata.not.ilike.EECC*)")
TANDA = 1000        # ids por borrado
PAUSA = 3           # segundos entre borrados


def contar() -> int:
    r = subir._pedir("GET", subir._url("ordenes_servicio") + f"?select=id_os&{FILTRO}&limit=1", "Conteo",
                     headers=subir._cabeceras({"Prefer": "count=exact"}))
    rango = r.headers.get("content-range", "*/0")
    return int(rango.split("/")[-1]) if "/" in rango else 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    total = contar()
    print(f"Filas de oficina a borrar: {total}", flush=True)
    if "--probar" in argv or not total:
        return 0
    borradas, t0 = 0, time.time()
    while True:
        r = subir._pedir("GET", subir._url("ordenes_servicio") + f"?select=id_os&{FILTRO}&limit=5000", "Lista de ids",
                         headers=subir._cabeceras())
        ids = [x["id_os"] for x in r.json()]
        if not ids:
            break
        for i in range(0, len(ids), TANDA):
            parte = ids[i:i + TANDA]
            t = time.time()
            subir._pedir("DELETE", subir._url("ordenes_servicio") + f"?id_os=in.({','.join(map(str, parte))})&{FILTRO}",
                         "Borrado", headers=subir._cabeceras({"Prefer": "return=minimal"}))
            borradas += len(parte)
            time.sleep(PAUSA + (10 if time.time() - t > 20 else 0))   # si la base se pone lenta, más pausa
        if time.time() - t0 > 3 * 3600:
            print("Se cortó a las 3 h; lo que falta se borra en la próxima corrida.", flush=True)
            break
    print(f"Listo: {borradas} filas borradas en {time.time() - t0:.0f} s. Quedan {contar()}.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
