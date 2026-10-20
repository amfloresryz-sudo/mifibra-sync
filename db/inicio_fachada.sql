-- FechaInicioFachada de ScordSoft: inicio de la instalación (columna «Iniciado» de Instalaciones › Pendientes).
alter table ordenes_servicio add column if not exists inicio_fachada timestamptz;
notify pgrst, 'reload schema';
