# Arma Speed Test con el código de ESTA carpeta (reglas actuales o nuevas) para las altas capturadas y lo guarda en JSON.
import json, os, pickle, sys
sys.path.insert(0, os.getcwd())
import sync.speedtest as ST
cap, cache_f, salida = sys.argv[1:4]
x, links, tec, corte = pickle.load(open(cap, "rb"))
tab = ST.construir(x, links, tec, corte)
cache = json.load(open(cache_f)) if os.path.exists(cache_f) else {}
ST.velocidades(tab, cache, limite=2000, segundos=400)
json.dump(cache, open(cache_f, "w"))
tab.pop("_enlaces", None)
crudo = {}
for r in x.itertuples(index=False):
    crudo[str(int(r.nro_os))] = {"obs": [[l[1], l[2]] for l in links.get(int(r.id_os), [])]}
json.dump({**tab, "actualizado": corte, "crudo": crudo}, open(salida, "w"), ensure_ascii=False, default=str)
print(f"{salida}: {len(tab['o'])} altas.", flush=True)
