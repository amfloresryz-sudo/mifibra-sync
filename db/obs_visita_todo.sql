-- Comentario completo del técnico (OBS VISITA TÉCNICO) de las altas, con teléfonos ocultos: ficha del AB.
alter table ordenes_servicio add column if not exists obs_visita_todo text;
notify pgrst, 'reload schema';
