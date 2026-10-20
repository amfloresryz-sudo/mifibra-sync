-- Cajas cercanas y última observación del técnico (módulo Instalaciones › Pendientes).
-- Las coordenadas del abonado NO se guardan: solo la lista de cajas a ≤200 m.
alter table ordenes_servicio add column if not exists cajas_cerca jsonb;
alter table ordenes_servicio add column if not exists obs_visita text;
alter table ordenes_servicio add column if not exists obs_visita_en text;
notify pgrst, 'reload schema';
