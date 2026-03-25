CREATE TABLE IF NOT EXISTS site_metadata (
    sitename VARCHAR(50) NOT NULL,
    x NUMERIC(15,5),
    y NUMERIC(15,5),
    z NUMERIC(15,5),
    target_crs_epsg INT,
    receiver VARCHAR(20),
    antenna VARCHAR(20),
    ecc_x NUMERIC(8,4) DEFAULT 0.0,
    ecc_y NUMERIC(8,4) DEFAULT 0.0,
    ecc_z NUMERIC(8,4) DEFAULT 0.0,
    valid_from DATE NOT NULL,
    valid_to DATE,
    PRIMARY KEY (sitename, valid_from)
);

CREATE UNIQUE INDEX ON site_metadata(sitename) WHERE valid_to IS NULL;

CREATE TABLE IF NOT EXISTS position (
    id BIGSERIAL,
    sitename VARCHAR(50) NOT NULL,
    x NUMERIC(15,5),
    y NUMERIC(15,5),
    z NUMERIC(15,5),
    dx NUMERIC(8,5),
    dy NUMERIC(8,5),
    dz NUMERIC(8,5),
    lat NUMERIC(12,7),
    lon NUMERIC(12,7),
    h_e NUMERIC(10,5),
    e NUMERIC(8,4),
    n NUMERIC(8,4),
    u NUMERIC(8,4),
    time_of_data TIMESTAMP WITH TIME ZONE NOT NULL,
    time_of_calc TIMESTAMP WITH TIME ZONE NOT NULL
);


CREATE INDEX ON position(sitename);
CREATE INDEX ON position(time_of_data);
CREATE UNIQUE INDEX ON position(sitename, time_of_data);


-- Example: register a site and its ECEF reference position
-- target_crs_epsg: EPSG code of the target CRS to transform ITRF2020 ECEF output into
--   e.g. 10890 = ETRS89-DNK geocentric; NULL = no transformation (stay in ITRF2020)
-- x, y, z: reference ECEF coordinates in the target CRS (used for ENU computation)
-- valid_from: date from which this reference position is valid
--
INSERT INTO site_metadata (sitename, x, y, z, target_crs_epsg, receiver, antenna, ecc_x, ecc_y, ecc_z, valid_from)
    VALUES ('BUDD00DNK', 3513649.62517, 778954.54558, 5248201.77774, 10890, 'SEPT POLARX5', 'LEIAR25.R4      LEIT', 0.0, 0.0, 2.6944, '2025-05-14');

INSERT INTO site_metadata (sitename, x, y, z, target_crs_epsg, receiver, antenna, ecc_x, ecc_y, ecc_z, valid_from)
    VALUES ('HANK00DNK', 3432374.12540, 519109.74682, 5332862.32916, 10890, 'SEPT POLARX5', 'LEIAR20         LEIM', 0.0, 0.0, 0.1756, '2025-05-14');

INSERT INTO site_metadata (sitename, x, y, z, target_crs_epsg, receiver, antenna, ecc_x, ecc_y, ecc_z, valid_from)
    VALUES ('MOJN00DNK', 3628427.91179, 562059.09356, 5197872.21496, 10890, 'SEPT POLARX5', 'LEIAR25.R4      LEIT', 0.0, 0.0, 0.18777, '2022-11-13');

INSERT INTO site_metadata (sitename, x, y, z, target_crs_epsg, receiver, antenna, ecc_x, ecc_y, ecc_z, valid_from)
    VALUES ('TEJH00DNK', 3522395.52033, 933244.48212, 5217231.27478, 10890, 'SEPT POLARX5', 'LEIAR25.R3      LEIT', 0.0, 0.0, 0.1683, '2022-11-13');
