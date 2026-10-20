-- Tipo de orden según TIPO_AB.xlsx de operaciones (columna DETALLE): AVERIA, TC, INSTALACION, TRASLADO EXT, …
-- La llena el robot (sync/procesar.py → detalle_de) en cada sincronización.
alter table ordenes_servicio add column if not exists detalle text;
create index if not exists ordenes_servicio_detalle_idx on ordenes_servicio (filial, detalle);
