# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`autoppp_ginan.py` is an automated daily GNSS Precise Point Positioning (PPP) pipeline. It:

1. Targets a specific date (currently hardcoded to 2 days ago via `range(2, 3)`)
2. Reads `config.json`, substituting `~WEEK~`, `~YEAR~`, `~DOY~` placeholders with GPS week, year, and day-of-year
3. Downloads PPP correction products (ERP, CLK, BIA, OBX, SP3) from an FTP server
4. Unpacks `.gz` files and converts Hatanaka-compressed `.crx` RINEX files to `.rnx` using `crx2rnx`
5. Generates a Ginan `pea` engine config from `ginan_template.yaml`, injecting observation files, correction products, and auxiliary resources (ATX antenna calibration, GPT2 troposphere grid, ocean tide BLQ)
6. Runs the Ginan `pea` binary to perform PPP, producing a GPX output
7. Processes observation files in parallel using `concurrent.futures.ThreadPoolExecutor` (capped at `ginan_instances` workers); each file gets its own `pea_config_{sitename}.yaml` to avoid conflicts
8. For each file: runs `pea`, parses the GPX output, converts coordinates, and writes results to the database

## Key Files

- `autoppp_ginan.py` — main pipeline script
- `config.json` — runtime configuration (FTP servers, file lists, directories); supports `~WEEK~`/`~YEAR~`/`~DOY~` placeholders
- `ginan_template.yaml` — base Ginan `pea` config that gets populated with per-run paths; placeholders like `~ATX~`, `~CLK~`, `~SP3~` etc. are replaced by `autoppp_ginan.py` at runtime

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

- **`observation_files`** — paths to RINEX observation files (`.crx.gz`); currently hardcoded to station `BUDP00DNK` on network mount `/mnt/refgps/GPSDATA/RINEX3/DNK/`
- **`resources_directory`** — local path to static auxiliary files (`./resources`)
- **`output_directory`** — working directory for downloads and outputs (`./workdir`)
- **`offline_input`** — static resource files: `igs20.atx` (antenna calibration), `gpt_25.grd` (troposphere), `FES2014B.BLQ` (ocean tides)
- **`ftp_servers`** — IGS FTP at `igs.ign.fr`; downloads CODE Rapid Analysis products (`COD0OPSRAP_*`): CLK, BIA (OSB), SP3, ERP, OBX
- **`pea_path`** — path to the Ginan `pea` binary (default: `/usr/bin/pea`)
- **`ginan_instances`** — maximum number of `pea` processes to run in parallel when processing multiple observation files

## Coordinate Conversions

Ginan outputs ECEF coordinates in **ITRF2020**. After parsing the GPX, `autoppp_ginan.py` converts using `pyproj`:

1. **ITRF2020 ECEF → target frame**: using a named PROJ transformation (e.g. `EPSG:10895` for ITRF2020→ETRS89); the transformation EPSG is stored per-site in `site_metadata.transform_epsg`; NULL means no transformation (stay in ITRF2020)
2. **Target ECEF → geographic (GRS80)**: inverse `proj=cart` pipeline step
3. **Target ECEF → ENU**: `proj=topocentric` relative to the reference position in `site_metadata`

## Database

Results are written to PostgreSQL using `psycopg2`. Connection is configured via environment variables (set in `docker-compose.yaml`):

| Variable | Default | Description |
|---|---|---|
| `DB_HOST` | `postgres` | Hostname (matches Docker service name) |
| `DB_PORT` | `5432` | Port |
| `DB_NAME` | `postgres` | Database name |
| `DB_USER` | `postgres` | Username |
| `DB_PASSWORD` | `password` | Password |

Schema (`initdb.sql`) — single table `position`:

| Column | Type | Description |
|---|---|---|
| `sitename` | VARCHAR(50) | GNSS station name |
| `x`, `y`, `z` | NUMERIC(10,5) | ITRF2020 ECEF coordinates |
| `dx`, `dy`, `dz` | NUMERIC(3,5) | ECEF coordinate corrections |
| `lat`, `lon`, `h_e` | NUMERIC(10,5) | Geographic coordinates on GRS80 |
| `time_of_data` | TIMESTAMP | Epoch of the GNSS observations |
| `time_of_calc` | TIMESTAMP | When the PPP solution was computed |

Indexed on `sitename`, `time_of_data`, and `(sitename, time_of_data)`.

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
- **`postgres`** — PostgreSQL (host port 9100→5432), initialized via `initdb.sql`
- **`autoppp`** — based on `gnssanalysis/ginan:v4.1.1` (`pea` bundled at `/usr/bin/pea`); mounts `./resources` read-only; DB connection passed via environment variables

Grafana is defined in `docker-compose.yaml` but commented out (planned for visualization).

**Building the Docker image:**
```bash
docker build -t autoppp_ginan:latest .
```

The container runs via `entrypoint.sh`, which writes `DB_*` environment variables to `/etc/autoppp_env` (so cron can access them) and then starts `cron -f`. A cron job in `/etc/cron.d/autoppp_ginan` runs `autoppp_ginan.py` daily at **01:00 UTC**, sourcing `/etc/autoppp_env` first. Output is logged to `/var/log/autoppp_ginan.log` inside the container.

**Directly (outside Docker):**
```bash
python autoppp_ginan.py                  # process 2 days ago (default)
python autoppp_ginan.py --days-back 7    # process 2 through 7 days ago
```
