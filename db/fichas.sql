-- Columnas de FFTT para la ficha de OS en Averías (vienen del mismo reporte de ScordSoft).
alter table ordenes_servicio add column if not exists zona_fftt text;
alter table ordenes_servicio add column if not exists nap text;
alter table ordenes_servicio add column if not exists borne text;
alter table ordenes_servicio add column if not exists dispositivo text;
notify pgrst, 'reload schema';
