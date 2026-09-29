-- Datos de ejemplo de los campos añadidos después (personas, consejos, aparatos).
-- Solo se ejecuta en una BBDD nueva, tras init.sql y migraciones.sql.

UPDATE recetas SET personas = v.personas FROM (VALUES
    ('Patata-tortilla', 4), ('Bakailaoa pil-pilean', 4), ('Pantxineta', 8), ('Tomate eta hegalabur entsalada', 2)
) AS v(titulo, personas) WHERE recetas.titulo = v.titulo;

INSERT INTO consejos (receta_id, orden, texto)
SELECT r.id, v.orden, v.texto FROM (VALUES
    ('Patata-tortilla', 1, 'Mamitsuago nahi baduzu, utzi arrautza eta patata nahasketa 10 minutuz pausatzen.'),
    ('Bakailaoa pil-pilean', 1, 'Olioa epela egon behar da, ez beroa; bestela saltsa ez da lotuko.'),
    ('Pantxineta', 1, 'Krema guztiz hotz dagoenean muntatu, hostorea bigundu ez dadin.'),
    ('Tomate eta hegalabur entsalada', 1, 'Tomatea giro-tenperaturan, ez hozkailutik aterata berehala.')
) AS v(receta, orden, texto)
JOIN recetas r ON r.titulo = v.receta;

INSERT INTO receta_aparatos (receta_id, aparato_id)
SELECT r.id, a.id FROM (VALUES
    ('Patata-tortilla', 'sarten'), ('Bakailaoa pil-pilean', 'sarten'), ('Pantxineta', 'horno')
) AS v(receta, aparato)
JOIN recetas r ON r.titulo = v.receta
JOIN aparatos a ON a.codigo = v.aparato;
