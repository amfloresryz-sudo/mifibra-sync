-- Agenda en ScordSoft desde los comentarios de la web: si el comentario trae día y franja, el robot (comentarios_scord)
-- además de escribirlo en OBS VISITA pone esa fecha y franja en la OS («Asignar Tecnico» → Agendar Orden de servicio).
--   ag_estado: null = el comentario no trae agenda · agendado = grabado y confirmado · igual = la OS ya tenía esa fecha y franja
--              reemplazado = hay un comentario más nuevo con agenda para la misma OS · omitido = no se agenda (motivo en ag_error:
--              fecha pasada, la OS ya no está registrada, sin técnico) · error = falló (no se reintenta si grabó sin confirmar)
alter table public.eecc_comentarios add column if not exists ag_estado text;
alter table public.eecc_comentarios add column if not exists ag_fecha  date;
alter table public.eecc_comentarios add column if not exists ag_franja text;
alter table public.eecc_comentarios add column if not exists ag_en     timestamptz;
alter table public.eecc_comentarios add column if not exists ag_error  text;
