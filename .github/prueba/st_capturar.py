# Prueba Speed Test (no escribe nada): corre el robot con el último reporte guardado y se detiene al armar Speed Test,
# guardando las altas de UNA filial y UN día (con sus enlaces y títulos) para comparar reglas.
import os, pickle, sys
import pandas as pd
sys.path.insert(0, os.getcwd())
import sync.main as M
import sync.speedtest as ST
salida, filial, dia, corte, *archivos = sys.argv[1:]

def capturar(df, links, tec, c):
    x = df[df["filial"].astype(str).str.upper() == filial].copy()
    fa = pd.to_datetime(x["fecha_atencion"], errors="coerce")
    x = x[fa.dt.strftime("%Y-%m-%d") == dia]
    pickle.dump((x, {int(i): links.get(int(i), []) for i in x["id_os"]}, tec, c), open(salida, "wb"))
    print(f"Capturadas {len(x)} órdenes de {filial} del {dia}.", flush=True)
    os._exit(0)

ST.construir = capturar
sys.exit(M.main(["--base", *archivos, "--sin-subir", "--corte", corte]))
