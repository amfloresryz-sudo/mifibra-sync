# Calibración (no escribe nada): lee de speedtest.net los datos públicos de una muestra de pruebas cuyo equipo
# (cliente/técnico) ya conocemos por el Control Operativo, para ver qué dato público lo distingue.
import base64, csv, gzip, json, sys, time
from concurrent.futures import ThreadPoolExecutor
import requests
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
filas = list(csv.DictReader(open(sys.argv[1])))
def leer(f):
    for intento in range(3):
        try:
            r = requests.get(f"https://www.speedtest.net/api/result/{f['id']}", headers=UA, timeout=10)
            if r.status_code == 404: return f, {"_404": 1}
            if r.status_code == 200: return f, r.json()
        except Exception: pass
        time.sleep(1 + intento)
    return f, None
out = []
with ThreadPoolExecutor(6) as ex:
    for f, j in ex.map(leer, filas):
        if isinstance(j, dict):   # sin datos personales: se quitan IP, lat/long, nombre de usuario
            for k in list(j):
                if any(x in k.lower() for x in ("ip", "lat", "lon", "user_name", "username", "email")): j.pop(k)
        out.append([f, j])
print("leídas", sum(1 for _, j in out if j), "de", len(out))
b = base64.b64encode(gzip.compress(json.dumps(out).encode())).decode()
print("=====INICIO equipo.json")
for i in range(0, len(b), 3000): print("B64:" + b[i:i + 3000])
print("=====FIN", flush=True)
