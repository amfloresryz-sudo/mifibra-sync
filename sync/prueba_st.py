"""Prueba (solo lectura): % de Speed Test de una filial y un día con distintas reglas. No escribe nada.
Uso: python -m sync.prueba_st LIMA 2026-11-11"""
import sys
from datetime import datetime, timezone

from . import subir

FIL, DIA = (sys.argv[1] if len(sys.argv) > 1 else "LIMA"), (sys.argv[2] if len(sys.argv) > 2 else "2026-11-11")
st, en, vel = subir.leer_tablero("speedtest"), subir.leer_tablero("speedtest_enlaces").get("o") or {}, subir.leer_tablero("speedtest_vel").get("v") or {}
fil = st.get("f") or []
filas = [f for f in st.get("o") or [] if f[1] == DIA and f[0] < len(fil) and fil[f[0]] == FIL]
dia = datetime.strptime(DIA, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() + 5 * 3600


def pruebas(f, sin_myresult):
    """[[quién, bajada, estado, es_myresult]] con las reglas actuales (otra red / no abre = no es prueba; ±1 día)."""
    minimo = 1200 if f[4] == 1 else 600
    out, pend = [], 0
    for clave, q in (en.get(f"{FIL}|{f[6]}") or [None, []])[1]:
        my = "my-result" in str(clave)
        if sin_myresult and my:
            continue
        v = vel.get(clave)
        if v is None:
            pend += 1
            continue
        if v[0] < 0 or (len(v) > 2 and not v[2]):
            continue
        if v[3] and not (dia - 86400 <= v[3] < dia + 2 * 86400):
            continue
        out.append([q or "T", v[0], "v" if v[0] > minimo else "n", my])
    return out, pend


def calcular(nombre, sin_myresult=False, uno_cliente=False):
    tot = len(filas)
    val = med = cli = pend = n_my = n_uno = 0
    for f in filas:
        P, p = pruebas(f, sin_myresult)
        pend += p
        n_my += sum(1 for x in P if x[3])
        if uno_cliente and len(P) == 1 and P[0][0] != "C":
            P[0][0] = "C"
            n_uno += 1
        if P:
            med += 1
        if any(x[0] == "C" for x in P):
            cli += 1
        if any(x[0] == "C" and x[2] == "v" for x in P):
            val += 1
    pc = lambda a: f"{a / tot * 100:5.1f}%" if tot else "  -  "
    print(f"{nombre:<58} Cumplimiento {pc(val)} ({val}/{tot}) · con medición {pc(med)} · medidas en equipo del cliente {pc(cli)}"
          f" · 1 sola prueba→cliente: {n_uno} · pruebas pendientes de leer: {pend}", flush=True)


print(f"Speed Test {FIL} {DIA}: {len(filas)} instalaciones (tablero actualizado {st.get('corte')})", flush=True)
calcular("A) Reglas actuales")
calcular("B) Una sola prueba = cliente", uno_cliente=True)
calcular("C) Sin enlaces my-result", sin_myresult=True)
calcular("D) Sin my-result + una sola prueba = cliente", sin_myresult=True, uno_cliente=True)
