CREATE TABLE IF NOT EXISTS sites (
    sitename VARCHAR(50) PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS site_coordinates (
    sitename VARCHAR(50) NOT NULL REFERENCES sites(sitename),
    x NUMERIC(15,5),
    y NUMERIC(15,5),
    z NUMERIC(15,5),
    valid_from DATE NOT NULL,
    valid_to DATE,
    PRIMARY KEY (sitename, valid_from)
);

CREATE UNIQUE INDEX ON site_coordinates(sitename) WHERE valid_to IS NULL;

CREATE TABLE IF NOT EXISTS position (
    id BIGSERIAL,
    sitename VARCHAR(50) NOT NULL REFERENCES sites(sitename),
    x NUMERIC(15,5),
    y NUMERIC(15,5),
    z NUMERIC(15,5),
    dx NUMERIC(8,5),
    dy NUMERIC(8,5),
    dz NUMERIC(8,5),
    lat NUMERIC(12,7),
    lon NUMERIC(12,7),
    h_e NUMERIC(10,5),
    utm_zone VARCHAR(3),
    easting NUMERIC(10,3),
    northing NUMERIC(10,3),
    time_of_data TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    time_of_calc TIMESTAMP WITHOUT TIME ZONE NOT NULL
);


CREATE INDEX ON position(sitename);
CREATE INDEX ON position(time_of_data);
CREATE UNIQUE INDEX ON position(sitename, time_of_data);
