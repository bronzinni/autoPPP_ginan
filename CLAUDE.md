# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`autoppp_ginan.py` is an automated daily GNSS Precise Point Positioning (PPP) pipeline. It:

Execution is split into three phases:

**Phase 1 (sequential, per-day):** For each day in the requested date range:
1. Reads `config.json`, substituting `~WEEK~`, `~YEAR~`, `~DOY~`, `~SITENAME~` placeholders
2. Creates a per-day workdir `{output_directory}/{year}_{doy}`
3. Downloads PPP correction products (ERP, CLK, BIA, OBX, SP3) from an FTP server into the workdir
4. Queries the `site_metadata` DB table to get the list of active sites for that day

**Phase 2 (parallel):** All jobs across all days are batched into a single `ThreadPoolExecutor` (capped at `ginan_instances` workers). For each site/day job:
5. Copies and unpacks the observation file (`.crx.gz` → `.rnx` via `gzip` + `crx2rnx`)
6. Generates a per-site Ginan `pea` config from `resources/ginan_template.yaml`, injecting observation files, correction products, and auxiliary resources; each site gets its own `pea_config_{sitename}.yaml`
7. Runs `pea`, parses the GPX output, converts coordinates, and writes results to the database

**Phase 3:** Cleans up each day's workdir after all jobs complete.

## Key Files

- `autoppp_ginan.py` — main pipeline script
- `config.json` — runtime configuration (FTP servers, file lists, directories); supports `~WEEK~`/`~YEAR~`/`~DOY~`/`~SITENAME~` placeholders
- `resources/ginan_template.yaml` — base Ginan `pea` config that gets populated with per-run paths at runtime by `autoppp_ginan.py`
- `db/initdb.sql` — PostgreSQL schema and example `site_metadata` seed data

## ginan_template.yaml Overview

The template configures the Ginan `pea` Kalman filter-based PPP engine:

- **GNSS constellations processed:** GPS, GLONASS, Galileo (BeiDou and QZSS disabled)
- **Output:** GPX file named `<RECEIVER>_autoppp_ginan_<YYYY><DDD><HH>.GPX`
- **Troposphere:** GPT2 model
- **Tides:** solid Earth, ocean tide loading (FES2014B), pole tides enabled; atmospheric tides disabled
- **Ionosphere:** uncombined PPP with per-epoch slant STEC estimation; 2nd and 3rd order corrections enabled
- **Elevation mask:** 10°; elevation-dependent error model
- **Estimated states:** receiver position (static, zero process noise), clock, ambiguities, ionospheric STEC, troposphere + gradients, code biases
- **RTS smoothing:** disabled
- **`wait_next_epoch: 3600`** — set large for post-processing (not real-time)

## config.json Structure

- **`observation_file_template`** — path template for RINEX observation files (`.crx.gz`) with `~SITENAME~` placeholder; sites are driven by the `site_metadata` DB table
- **`resources_directory`** — local path to static auxiliary files (`./resources`)
- **`output_directory`** — working directory for downloads and outputs (`./workdir`)
- **`offline_input`** — static resource files: `igs20.atx` (antenna calibration), `gpt_25.grd` (troposphere), `FES2014B.BLQ` (ocean tides)
- **`ftp_servers`** — IGS FTP at `igs.ign.fr`; downloads CODE Rapid Analysis products (`COD0OPSRAP_*`): CLK, BIA (OSB), SP3, ERP, OBX
- **`pea_path`** — path to the Ginan `pea` binary (default: `/usr/bin/pea`)
- **`ginan_instances`** — maximum number of `pea` processes to run in parallel when processing multiple observation files

## Coordinate Conversions

Ginan outputs ECEF coordinates in **ITRF2020**. After parsing the GPX, `autoppp_ginan.py` converts using `pyproj`:

1. **ITRF2020 ECEF → target frame**: using a named PROJ transformation (e.g. `EPSG:10890` for ITRF2020→ETRS89-DNK geocentric); the EPSG is stored per-site in `site_metadata.target_crs_epsg`; NULL means no transformation (stay in ITRF2020)
2. **Target ECEF → geographic (GRS80)**: inverse `proj=cart` pipeline step
3. **Target ECEF → ENU**: `proj=topocentric` relative to the reference position (`x`, `y`, `z` in the target CRS) stored in `site_metadata`

## Database

Results are written to PostgreSQL using `psycopg2`. Connection is configured via environment variables (set in `docker-compose.yaml`):

| Variable | Default | Description |
|---|---|---|
| `DB_HOST` | `postgres` | Hostname (matches Docker service name) |
| `DB_PORT` | `5432` | Port |
| `DB_NAME` | `postgres` | Database name |
| `DB_USER` | `postgres` | Username |
| `DB_PASSWORD` | `password` | Password |

Schema (`db/initdb.sql`) — two tables:

**`site_metadata`** — active sites and their reference data:

| Column | Type | Description |
|---|---|---|
| `sitename` | VARCHAR(50) | GNSS station name (PK with `valid_from`) |
| `x`, `y`, `z` | NUMERIC(15,5) | Reference ECEF in target CRS (for ENU) |
| `target_crs_epsg` | INT | EPSG of target CRS; NULL = stay in ITRF2020 |
| `receiver` | VARCHAR(20) | Receiver type string for `pea` |
| `antenna` | VARCHAR(20) | Antenna type string for `pea` |
| `ecc_x/y/z` | NUMERIC(8,4) | Antenna eccentricity offsets |
| `valid_from` | DATE | Start of validity (PK with `sitename`) |
| `valid_to` | DATE | End of validity; NULL = currently active |

**`position`** — PPP results:

| Column | Type | Description |
|---|---|---|
| `id` | BIGSERIAL | Auto-incrementing PK |
| `sitename` | VARCHAR(50) | GNSS station name |
| `x`, `y`, `z` | NUMERIC(15,5) | ECEF coordinates in target CRS |
| `dx`, `dy`, `dz` | NUMERIC(8,5) | ECEF coordinate corrections |
| `lat`, `lon` | NUMERIC(12,7) | Geographic coordinates on GRS80 |
| `h_e` | NUMERIC(10,5) | Ellipsoidal height on GRS80 |
| `e`, `n`, `u` | NUMERIC(8,4) | ENU offsets from reference position |
| `time_of_data` | TIMESTAMP WITH TIME ZONE | Epoch of the GNSS observations |
| `time_of_calc` | TIMESTAMP WITH TIME ZONE | When the PPP solution was computed |

Indexed on `sitename`, `time_of_data`, and unique index on `(sitename, time_of_data)`.

## Dependencies

- Python packages: `pyyaml`, `pyproj`, `psycopg2`
- External tools: `gzip`, `crx2rnx` (Hatanaka RINEX converter), Ginan `pea` binary
- Ginan `pea` binary path configured via `pea_path` in `config.json` (defaults to `/usr/bin/pea` as provided by the `gnssanalysis/ginan:v4.1.1` Docker image)

## Running

**Via Docker Compose (primary deployment method):**
```bash
docker compose up
```

Services:
- **`postgres`** — PostgreSQL (host port 9100→5432), initialized via `db/initdb.sql`
- **`grafana`** — Grafana OSS (host port 3000→3000); provisioning config from `./grafana/`; visualizes PPP results from PostgreSQL
- **`autoppp_ginan`** — based on `gnssanalysis/ginan:v4.1.1` (`pea` bundled at `/usr/bin/pea`); mounts `./resources` and `/mnt/refgps` read-only, `./logs` for log output; DB connection passed via environment variables

**Building the Docker image:**
```bash
docker build -t autoppp_ginan:latest .
```

The container runs via `entrypoint.sh`, which writes `DB_*` environment variables to `/etc/autoppp_env` (so cron can access them) and then starts `cron -f`. A cron job in `/etc/cron.d/autoppp_ginan` runs `autoppp_ginan.py` daily at **01:00 UTC**, sourcing `/etc/autoppp_env` first. Output is logged to `./logs/autoppp_ginan.log` (mounted from the host).

**Directly (outside Docker):**
```bash
python autoppp_ginan.py                                   # process 2 days ago (default)
python autoppp_ginan.py --from-days-back 7                # process 7 days ago only
python autoppp_ginan.py --from-days-back 2 --to-days-back 7  # process days 2–7 ago
```
