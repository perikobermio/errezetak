-- Esquema de la web de recetas.
-- Cada parte de una receta vive en su propia tabla: tags, imagen,
-- ingredientes, pasos de preparación y tiempos de cocción.

CREATE TABLE recetas (
    id          SERIAL PRIMARY KEY,
    titulo      VARCHAR(200) NOT NULL,
    descripcion TEXT NOT NULL DEFAULT '',
    raciones    INTEGER CHECK (raciones > 0),
    creada_en   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tags (
    id     SERIAL PRIMARY KEY,
    nombre VARCHAR(50) NOT NULL UNIQUE
);

CREATE TABLE receta_tags (
    receta_id INTEGER NOT NULL REFERENCES recetas(id) ON DELETE CASCADE,
    tag_id    INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (receta_id, tag_id)
);
CREATE INDEX idx_receta_tags_tag ON receta_tags(tag_id);

-- Una imagen por receta, guardada en la propia BBDD.
CREATE TABLE imagenes (
    id         SERIAL PRIMARY KEY,
    receta_id  INTEGER NOT NULL UNIQUE REFERENCES recetas(id) ON DELETE CASCADE,
    nombre     VARCHAR(255) NOT NULL,
    mime       VARCHAR(100) NOT NULL,
    datos      BYTEA NOT NULL
);

CREATE TABLE ingredientes (
    id        SERIAL PRIMARY KEY,
    receta_id INTEGER NOT NULL REFERENCES recetas(id) ON DELETE CASCADE,
    orden     INTEGER NOT NULL,
    nombre    VARCHAR(200) NOT NULL,
    cantidad  VARCHAR(100) NOT NULL DEFAULT ''
);
CREATE INDEX idx_ingredientes_receta ON ingredientes(receta_id, orden);

CREATE TABLE pasos_preparacion (
    id        SERIAL PRIMARY KEY,
    receta_id INTEGER NOT NULL REFERENCES recetas(id) ON DELETE CASCADE,
    orden     INTEGER NOT NULL,
    texto     TEXT NOT NULL
);
CREATE INDEX idx_pasos_receta ON pasos_preparacion(receta_id, orden);

-- Varios tiempos por receta: preparación, horno, reposo, etc.
CREATE TABLE tiempos_coccion (
    id        SERIAL PRIMARY KEY,
    receta_id INTEGER NOT NULL REFERENCES recetas(id) ON DELETE CASCADE,
    orden     INTEGER NOT NULL,
    fase      VARCHAR(100) NOT NULL,
    minutos   INTEGER NOT NULL CHECK (minutos >= 0)
);
CREATE INDEX idx_tiempos_receta ON tiempos_coccion(receta_id, orden);

-- ---------------------------------------------------------------
-- Datos de ejemplo (en euskera)
-- ---------------------------------------------------------------
INSERT INTO tags (nombre) VALUES
    ('begetarianoa'), ('tradizionala'), ('postrea'), ('labea'), ('azkarra'), ('arraina'), ('glutenik gabe');

INSERT INTO recetas (titulo, descripcion, raciones) VALUES
    ('Patata-tortilla', 'Klasikoa: barrutik mamitsua eta tipularekin.', 4),
    ('Bakailaoa pil-pilean', 'Bakailao gezatua, bere gelatinarekin eta oliba-olioarekin lotua.', 4),
    ('Pantxineta', 'Krema pastelaz betetako hostorea, almendra xerrekin.', 8),
    ('Tomate eta hegalabur entsalada', 'Sasoiko tomatea, olioztatutako hegalaburra eta tipulina.', 2);

INSERT INTO receta_tags (receta_id, tag_id)
SELECT r.id, t.id FROM (VALUES
    ('Patata-tortilla', 'begetarianoa'), ('Patata-tortilla', 'tradizionala'), ('Patata-tortilla', 'glutenik gabe'),
    ('Bakailaoa pil-pilean', 'arraina'), ('Bakailaoa pil-pilean', 'tradizionala'), ('Bakailaoa pil-pilean', 'glutenik gabe'),
    ('Pantxineta', 'postrea'), ('Pantxineta', 'labea'), ('Pantxineta', 'tradizionala'),
    ('Tomate eta hegalabur entsalada', 'azkarra'), ('Tomate eta hegalabur entsalada', 'arraina'), ('Tomate eta hegalabur entsalada', 'glutenik gabe')
) AS v(receta, tag)
JOIN recetas r ON r.titulo = v.receta
JOIN tags t ON t.nombre = v.tag;

INSERT INTO ingredientes (receta_id, orden, nombre, cantidad)
SELECT r.id, v.orden, v.nombre, v.cantidad FROM (VALUES
    ('Patata-tortilla', 1, 'Patatak', '800 g'),
    ('Patata-tortilla', 2, 'Arrautzak', '6'),
    ('Patata-tortilla', 3, 'Tipula', '1'),
    ('Patata-tortilla', 4, 'Oliba-olioa', '300 ml'),
    ('Patata-tortilla', 5, 'Gatza', 'nahi adina'),
    ('Bakailaoa pil-pilean', 1, 'Bakailao gezatuaren solomoak', '4'),
    ('Bakailaoa pil-pilean', 2, 'Oliba-olio birjina', '250 ml'),
    ('Bakailaoa pil-pilean', 3, 'Baratxuria', '4 ale'),
    ('Bakailaoa pil-pilean', 4, 'Piperrmina', '1'),
    ('Pantxineta', 1, 'Hostore-xaflak', '2'),
    ('Pantxineta', 2, 'Esnea', '500 ml'),
    ('Pantxineta', 3, 'Arrautza-gorringoak', '4'),
    ('Pantxineta', 4, 'Azukrea', '100 g'),
    ('Pantxineta', 5, 'Arto-irina (maizena)', '40 g'),
    ('Pantxineta', 6, 'Almendra xerratan', '80 g'),
    ('Tomate eta hegalabur entsalada', 1, 'Tomate helduak', '2'),
    ('Tomate eta hegalabur entsalada', 2, 'Hegalaburra olioan', 'lata 1'),
    ('Tomate eta hegalabur entsalada', 3, 'Tipulina', '1/2'),
    ('Tomate eta hegalabur entsalada', 4, 'Olioa, ozpina eta gatza', 'nahi adina')
) AS v(receta, orden, nombre, cantidad)
JOIN recetas r ON r.titulo = v.receta;

INSERT INTO pasos_preparacion (receta_id, orden, texto)
SELECT r.id, v.orden, v.texto FROM (VALUES
    ('Patata-tortilla', 1, 'Zuritu patatak eta ebaki xafla finetan; tipula, juliana erara.'),
    ('Patata-tortilla', 2, 'Konfitatu olio ugaritan, su ertainean, bigundu arte. Xukatu.'),
    ('Patata-tortilla', 3, 'Irabiatu arrautzak gatzarekin, nahastu patatekin eta utzi 5 minutuz pausatzen.'),
    ('Patata-tortilla', 4, 'Mamitu zartagin batean olio pixka batekin, eman buelta plater batekin eta amaitu.'),
    ('Bakailaoa pil-pilean', 1, 'Gorritu baratxuri xerrak eta piperrmina olioan, su motelean. Atera eta gorde.'),
    ('Bakailaoa pil-pilean', 2, 'Utzi olioa epeltzen eta konfitatu bakailaoa, azala gorantz duela.'),
    ('Bakailaoa pil-pilean', 3, 'Atera bakailaoa eta lotu olioa, kazola biribilka mugituz edo iragazki batekin.'),
    ('Bakailaoa pil-pilean', 4, 'Sartu berriro bakailaoa saltsan eta apaindu baratxuriarekin eta piperrminarekin.'),
    ('Pantxineta', 1, 'Prestatu krema pastela esnearekin, gorringoekin, azukrearekin eta maizenarekin. Utzi hozten.'),
    ('Pantxineta', 2, 'Zabaldu hostore-xafla bat, estali kremarekin eta jarri beste xafla gainean.'),
    ('Pantxineta', 3, 'Margotu arrautzaz, estali almendra xerrekin eta erre labean 190 °C-tan.'),
    ('Pantxineta', 4, 'Hautseztatu azukre glasarekin zerbitzatu aurretik.'),
    ('Tomate eta hegalabur entsalada', 1, 'Ebaki tomatea xerra lodietan eta tipulina eraztun finetan.'),
    ('Tomate eta hegalabur entsalada', 2, 'Jarri hegalaburra gainean eta ondu olioarekin, ozpinarekin eta gatzarekin.')
) AS v(receta, orden, texto)
JOIN recetas r ON r.titulo = v.receta;

INSERT INTO tiempos_coccion (receta_id, orden, fase, minutos)
SELECT r.id, v.orden, v.fase, v.minutos FROM (VALUES
    ('Patata-tortilla', 1, 'Prestaketa', 15),
    ('Patata-tortilla', 2, 'Patata konfitatzea', 25),
    ('Patata-tortilla', 3, 'Mamitzea', 6),
    ('Bakailaoa pil-pilean', 1, 'Prestaketa', 10),
    ('Bakailaoa pil-pilean', 2, 'Konfitatzea', 15),
    ('Bakailaoa pil-pilean', 3, 'Lotzea', 10),
    ('Pantxineta', 1, 'Krema pastela', 15),
    ('Pantxineta', 2, 'Hoztea', 60),
    ('Pantxineta', 3, 'Labea', 30),
    ('Tomate eta hegalabur entsalada', 1, 'Prestaketa', 10)
) AS v(receta, orden, fase, minutos)
JOIN recetas r ON r.titulo = v.receta;
