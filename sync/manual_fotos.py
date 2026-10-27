"""Manual de evidencias fotográficas — LIQUIDACIÓN DE INSTALACIONES (Lima), Área de Operaciones.

Transcrito del PPT "LIQUIDACIÓN INSTALACIONES - LIMA - FOTOS". Si el manual cambia, se edita solo esta lista.
Reglas generales acordadas con Operaciones:
  * Toda foto debe mostrar el sello de localización, fecha y hora (salvo capturas de pantalla del gestor: mapa y parámetros).
  * Se aceptan fotos con fecha del día de ejecución o del día anterior.
"""

DIAS_TOLERANCIA = 1  # fotos del día anterior a la ejecución son válidas
PERDIDA_MAX_DB = 1.0  # foto 2: pérdida máxima permitida entre la potencia en caja (NAP) y en el cliente

MANUAL = [
    {"n": 1, "nombre": "Fachada del cliente", "debe": [
        "Fachada de la vivienda fácil de identificar (número de puerta visible)",
        "Sello con localización, fecha y hora"]},
    {"n": 2, "nombre": "Potencia en NAP y en cliente", "debe": [
        "Medición de potencia en la NAP dentro de rangos aceptables (aprox. -15 a -25 dBm)",
        "Se ve que la medición se hizo en ambos lugares (NAP y domicilio del cliente)",
        "Valores del power meter legibles",
        f"Pérdida entre caja y cliente de máximo {PERDIDA_MAX_DB} dB: |potencia NAP − potencia cliente| ≤ {PERDIDA_MAX_DB}"
        " (si es mayor = ERROR, indicando ambos valores y la diferencia)",
        "Sello con localización, fecha y hora"]},
    {"n": 3, "nombre": "NAP cerrada y DROP conectado al borne", "debe": [
        "Serie/código de la CTO visible",
        "CTO/NAP cerrada",
        "DROP conectado en la parte inferior de la CTO",
        "Cintillo visible",
        "Sello con localización, fecha y hora"]},
    {"n": 4, "nombre": "Ubicación del router (ONU)", "debe": [
        "ONU fijada en la pared del cliente (no sobre mesa/escritorio)",
        "Estética correcta, a 1.5–2 m sobre el piso",
        "Sello con localización, fecha y hora"]},
    {"n": 5, "nombre": "DNI y boletín", "debe": [
        "Datos del DNI visibles claramente",
        "Boletín con firma del cliente y del técnico",
        "Sello con localización, fecha y hora"]},
    {"n": 6, "nombre": "Borne y cintillo rosado", "debe": [
        "Cintillo rosado claramente visible",
        "Borne utilizado identificable",
        "Sello con localización, fecha y hora"]},
    {"n": 7, "nombre": "Speedtest desde la computadora del cliente", "debe": [
        "Prueba con la app Speedtest, con adaptador Ethernet",
        "Si el cliente no tiene dispositivo, evidencia de ello",
        "Sello con hora, fecha y geolocalización"]},
    {"n": 8, "nombre": "Speedtest desde el dispositivo del técnico (de preferencia laptop)", "debe": [
        "Desde aplicativo o navegador incógnito",
        "Si es dispositivo de gama baja, evidencia del dispositivo",
        "Sello con hora, fecha y geolocalización"]},
    {"n": 9, "nombre": "WiFi (2.4G y 5G) y remoto activo", "debe": [
        "Configuración WiFi 2.4G y 5G visible correctamente",
        "Acceso remoto activo visible"]},
    {"n": 10, "nombre": "Vinculación MESH y potencia MESH", "solo_si_mesh": True, "debe": [
        "Topología/vinculación del MESH con el router",
        "Potencia del MESH legible: -30 a -50 excelente, -51 a -60 muy buena, -61 a -70 buena, -71 o menos débil (débil = error)"]},
    {"n": 11, "nombre": "Mapa de CTO que brinda el gestor", "sin_sello": True, "debe": [
        "Captura del mapa de CTOs del gestor"]},
    {"n": 12, "nombre": "Envío de parámetros", "sin_sello": True, "debe": [
        "Captura del envío de parámetros por parte del gestor MF"]},
]


def texto_manual(con_mesh: bool) -> str:
    lineas = []
    for item in MANUAL:
        if item.get("solo_si_mesh") and not con_mesh:
            continue
        lineas.append(f"Foto {item['n']} – {item['nombre']}:")
        lineas += [f"   - {d}" for d in item["debe"]]
    return "\n".join(lineas)
