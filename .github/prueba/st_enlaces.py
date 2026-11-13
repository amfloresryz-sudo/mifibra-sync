# Calibración (no escribe nada): vuelca, para las altas de los días pedidos (todas las filiales), los enlaces de speedtest
# de «Observaciones Técnico» con el título que detecta el robot. Se compara después con la exportación de Ookla.
import base64, gzip, json, os, sys
import pandas as pd
sys.path.insert(0, os.getcwd())
import sync.main as M
import sync.speedtest as ST
dias, corte, *archivos = sys.argv[1:]
import datetime as _d
d0, d1 = dias.split(":")
dias = {str(_d.date.fromisoformat(d0) + _d.timedelta(days=i)) for i in range((_d.date.fromisoformat(d1) - _d.date.fromisoformat(d0)).days + 1)}

def capturar(df, links, tec, c):
    x = df[(df["tipo_orden"] == "INSTALACION SERVICIO") & (df["estado_os"] == "DESCARGADA")].copy()
    fa = pd.to_datetime(x["fecha_atencion"], errors="coerce").dt.strftime("%Y-%m-%d")
    x = x[fa.isin(dias)]
    out = [[str(r.filial), int(r.nro_os), str(fa[i]), str(r.contrata), str(r.tecnico_ejecutor), [[l[0], l[1], l[2]] for l in links.get(int(r.id_os), [])]]
           for i, r in zip(x.index, x.itertuples(index=False))]
    b = base64.b64encode(gzip.compress(json.dumps(out, ensure_ascii=False).encode())).decode()
    print(f"{len(out)} altas.")
    print("=====INICIO enlaces.json")
    for i in range(0, len(b), 3000):
        print("B64:" + b[i:i + 3000])
    print("=====FIN", flush=True)
    os._exit(0)

ST.construir = capturar
sys.exit(M.main(["--base", *archivos, "--sin-subir", "--corte", corte]))
