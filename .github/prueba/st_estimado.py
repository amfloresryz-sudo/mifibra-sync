# PRUEBA (no escribe nada): arma Speed Test con el código de esta rama sobre el último reporte guardado.
# (a) normal: archivo de control hasta el 11/11 → el 12/11 sale ESTIMADO.
# (b) prueba ciega: se esconde el control desde el 10/11 → se estiman 10 y 11/11 y se comparan con lo real de Ookla.
import base64, copy, gzip, json, os, sys
from collections import defaultdict
sys.path.insert(0, os.getcwd())
import sync.main as M
import sync.speedtest as ST
from sync import subir
corte, *archivos = sys.argv[1:]
CAP = {}
orig = ST.construir
def capturar(df, links, tec, c):
    CAP["t"] = orig(df, links, tec, c)
    raise SystemExit("capturado")
ST.construir = capturar
try:
    M.main(["--base", *archivos, "--sin-subir", "--corte", corte])
except SystemExit as e:
    print("captura:", e, flush=True)
tab0 = CAP["t"]
cache = {k: v for k, v in (subir.leer_tablero("speedtest_vel").get("v") or {}).items()}
print("caché", len(cache), flush=True)
ref_full = ST._referencia()
fil = tab0["f"]
def resumen(tab):
    R = defaultdict(lambda: [0, 0, 0])
    for f in tab["o"]:
        if f[1] < "2026-11-01": continue
        P = f[12] if len(f) > 12 else []
        v = any(p[0] == "C" and p[3] == "v" for p in P)
        for g in (fil[f[0]], "TODAS"):
            r = R[(f[1], g)]; r[0] += 1; r[1] += v; r[2] += (len(f) > 13 and f[13] == 1)
    return {f"{d}|{g}": x for (d, g), x in R.items()}
ta = copy.deepcopy(tab0); ST.velocidades(ta, dict(cache), limite=0, segundos=0); ra = resumen(ta)
ocultar = {f"{fil[f[0]]}|{f[6]}" for f in tab0["o"] if f[1] >= "2026-11-10"}
ST._referencia = lambda: {k: v for k, v in ref_full.items() if k not in ocultar}
tb = copy.deepcopy(tab0); ST.velocidades(tb, dict(cache), limite=0, segundos=0); rb = resumen(tb)
ec = defaultdict(lambda: [0, 0, 0, 0])
for fa, fb in zip(ta["o"], tb["o"]):
    if fa[1] in ("2026-11-10", "2026-11-11") and fil[fa[0]] == "LIMA":
        k = ta["e"][fa[2]]; e = ec[k]; e[0] += 1
        e[1] += any(p[0] == "C" and p[3] == "v" for p in fa[12]); e[2] += any(p[0] == "C" and p[3] == "v" for p in fb[12])
lima = {"f": ta["f"], "e": ta["e"], "k": ta["k"], "pn": ta["pn"], "rep": [], "actualizado": corte,
        "o": [f for f in ta["o"] if ta["f"][f[0]] == "LIMA" and f[1] >= "2026-11-01"]}
out = {"a": ra, "b": rb, "ec": ec, "lima": lima}
b = base64.b64encode(gzip.compress(json.dumps(out, ensure_ascii=False).encode())).decode()
print("=====INICIO estimado.json")
for i in range(0, len(b), 3000): print("B64:" + b[i:i + 3000])
print("=====FIN", flush=True)
