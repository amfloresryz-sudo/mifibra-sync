# Arma Speed Test con el código de ESTA carpeta (reglas actuales o nuevas) para las altas capturadas y lo guarda en JSON.
import json, os, pickle, sys
sys.path.insert(0, os.getcwd())
import sync.speedtest as ST
cap, cache_f, salida = sys.argv[1:4]
x, links, tec, corte = pickle.load(open(cap, "rb"))
tab = ST.construir(x, links, tec, corte)
if os.path.exists(cache_f):
    cache = json.load(open(cache_f))
else:   # copia de las velocidades ya leídas que guarda el robot en Supabase (solo LECTURA)
    from sync import subir
    cache = {k: v for k, v in (subir.leer_tablero("speedtest_vel").get("v") or {}).items()}
    print(f"Caché de velocidades: {len(cache)} enlaces.", flush=True)
claves = {str(r[6]): [[c, q] for c, q in (r[12] if len(r) > 12 else [])] for r in tab["o"]}   # enlaces de cada alta (antes de leer velocidades)
ST.velocidades(tab, cache, limite=2000, segundos=400)
tab["_claves"] = claves
tab["_vel"] = {c: cache.get(c) for v in claves.values() for c, _ in v}   # [bajada, subida, red ok, fecha (epoch)] de cada enlace
json.dump(cache, open(cache_f, "w"))
tab.pop("_enlaces", None)
crudo = {}
for r in x.itertuples(index=False):
    crudo[str(int(r.nro_os))] = {"obs": [[l[1], l[2]] for l in links.get(int(r.id_os), [])]}
json.dump({**tab, "actualizado": corte, "crudo": crudo}, open(salida, "w"), ensure_ascii=False, default=str)
print(f"{salida}: {len(tab['o'])} altas.", flush=True)
import base64, gzip
b = base64.b64encode(gzip.compress(open(salida, "rb").read())).decode()
print("=====INICIO " + os.path.basename(salida))
for i in range(0, len(b), 3000):
    print("B64:" + b[i:i + 3000])
print("=====FIN")
