-- Migraciones idempotentes: la app las ejecuta en cada arranque.
-- Permiten añadir campos a una BBDD ya existente sin perder datos
-- (db/init.sql solo se ejecuta cuando el volumen está vacío).

-- Para cuántas personas es la receta (distinto de las raciones: "6 tortitas").
ALTER TABLE recetas ADD COLUMN IF NOT EXISTS personas INTEGER CHECK (personas > 0);

-- Consejos / tips.
CREATE TABLE IF NOT EXISTS consejos (
    id        SERIAL PRIMARY KEY,
    receta_id INTEGER NOT NULL REFERENCES recetas(id) ON DELETE CASCADE,
    orden     INTEGER NOT NULL,
    texto     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_consejos_receta ON consejos(receta_id, orden);

-- Aparatos de cocina (catálogo) y los que usa cada receta.
CREATE TABLE IF NOT EXISTS aparatos (
    id     SERIAL PRIMARY KEY,
    codigo VARCHAR(30) NOT NULL UNIQUE,
    nombre VARCHAR(100) NOT NULL,
    icono  VARCHAR(10) NOT NULL DEFAULT '',
    orden  INTEGER NOT NULL DEFAULT 0
);
INSERT INTO aparatos (codigo, nombre, icono, orden) VALUES
    ('airfryer',   'Airfryer-a', '💨', 1),
    ('horno',      'Labea',      '🔥', 2),
    ('microondas', 'Mikrouhina', '⚡', 3),
    ('sarten',     'Zartagina',  '🍳', 4)
ON CONFLICT (codigo) DO NOTHING;

CREATE TABLE IF NOT EXISTS receta_aparatos (
    receta_id  INTEGER NOT NULL REFERENCES recetas(id) ON DELETE CASCADE,
    aparato_id INTEGER NOT NULL REFERENCES aparatos(id) ON DELETE CASCADE,
    PRIMARY KEY (receta_id, aparato_id)
);

-- URL de la que se descargó la imagen (la imagen en sí se guarda en `datos`).
ALTER TABLE imagenes ADD COLUMN IF NOT EXISTS url_origen TEXT;
