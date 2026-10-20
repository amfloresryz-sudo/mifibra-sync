-- Columnas para el módulo Instalaciones (vienen del mismo reporte de ScordSoft).
alter table ordenes_servicio add column if not exists servicio text;
alter table ordenes_servicio add column if not exists plano text;
alter table ordenes_servicio add column if not exists tipo_vivienda text;
alter table ordenes_servicio add column if not exists fecha_programada date;  -- "Fecha Asignacion": día de la ruta del técnico
alter table ordenes_servicio add column if not exists fecha_cita date;        -- "Fecha Atencion": cita ofrecida en la venta
notify pgrst, 'reload schema';
