import argparse
import concurrent.futures
import fnmatch
import json
import datetime
import logging

import os
import shutil
import subprocess

from ftplib import FTP

import yaml

import psycopg2

from dataclasses import dataclass

from pyproj import Transformer


_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_log_dir, exist_ok=True)
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s",
                    filename=os.path.join(_log_dir, "autoppp_ginan.log"))
logger = logging.getLogger(__name__)

SECONDS_OF_WEEK = 7*24*60*60
GPS_START = datetime.datetime(year=1980, month=1, day=6, tzinfo=datetime.timezone.utc)


@dataclass
class SiteJob:
    sitename: str
    obs_file: str
    target_crs_epsg: int
    ref_x: float
    ref_y: float
    ref_z: float
    receiver: str
    antenna: str
    ecc_x: float
    ecc_y: float
    ecc_z: float

    @classmethod
    def from_site_row(cls, row, config):
        sitename, target_crs_epsg, ref_x, ref_y, ref_z, receiver, antenna, ecc_x, ecc_y, ecc_z = row
        obs_file = config.replace_placeholders(config.config["observation_file_template"], sitename)
        return cls(sitename, obs_file, target_crs_epsg, ref_x, ref_y, ref_z, receiver, antenna, ecc_x, ecc_y, ecc_z)


class Config():
    def __init__(self, time_of_data: datetime.datetime):
        self.time_of_data = time_of_data

        self.week = str(int((time_of_data-GPS_START).total_seconds()/SECONDS_OF_WEEK))
        self.year = time_of_data.strftime("%Y")
        self.doy  = time_of_data.strftime("%j")

        with open('config.json', 'r') as config_file:
            # read in config as string
            config_file_str = config_file.read()

            # replace placeholders with correct values
            config_file_str = self.replace_placeholders(config_file_str)
    
            # read json into dictionary 
            self.config = json.loads(config_file_str)

#            for key, item in self.config.items():
#                print(key, item)
#        
    def replace_placeholders(self, string: str, sitename: str = None):
        result = (string.replace("~WEEK~", self.week)
                        .replace("~YEAR~", self.year)
                        .replace("~DOY~", self.doy))
        if sitename is not None:
            result = result.replace("~SITENAME~", sitename)
            country = sitename[-3:]
            result = result.replace("~COUNTRY~", country)
        return result


def process_obs_file(job: SiteJob, config, workdir, product_path_dict, autoppp_directory):
    import xml.etree.ElementTree as ET

    obs_file_storage_path = os.path.join(autoppp_directory, job.obs_file)
    obs_file_workdir_path = os.path.join(workdir, os.path.basename(job.obs_file))

    if not os.path.exists(obs_file_storage_path):
        raise FileNotFoundError(f"Observation file not found: {obs_file_storage_path}")

    subprocess.run(["cp", obs_file_storage_path, obs_file_workdir_path])

    obs_file_workdir_path = unpack(obs_file_workdir_path)

    ginan_template_path = os.path.join(autoppp_directory, "resources", "ginan_template.yaml")
    with open(ginan_template_path) as yaml_file:
        ginan_template = yaml.load(yaml_file, Loader=yaml.FullLoader)

    ginan_template['inputs']['inputs_root'] = autoppp_directory
    resources_directory = os.path.join(autoppp_directory, config.config["resources_directory"])
    ginan_template['inputs']['atx_files'] = [os.path.join(resources_directory, config.config["offline_input"]["ATX"])]
    ginan_template['inputs']['troposphere']['gpt2grid_files'] = [os.path.join(resources_directory, config.config["offline_input"]["gpt2"])]
    ginan_template['inputs']['tides']['ocean_tide_loading_blq_files'] = [os.path.join(resources_directory, config.config["offline_input"]["ocean_tide"])]
    ginan_template['inputs']['gnss_observations']['gnss_observations_root'] = workdir
    ginan_template['inputs']['gnss_observations']['rnx_inputs'] = [obs_file_workdir_path]
    ginan_template['outputs']['outputs_root'] = workdir
    gpx_filename = config.replace_placeholders(ginan_template['outputs']['gpx']['filename'], job.sitename)
    ginan_template['outputs']['gpx']['filename'] = gpx_filename

    ginan_template['inputs']['erp_files'] = [product_path_dict["ERP"]]
    ginan_template['inputs']['satellite_data']['clk_files'] = [product_path_dict["CLK"]]
    ginan_template['inputs']['satellite_data']['bsx_files'] = [product_path_dict["BIA"], product_path_dict["OBX"]]
    ginan_template['inputs']['satellite_data']['sp3_files'] = [product_path_dict["SP3"]]

    site_code = job.sitename[:4]
    site_options = {
        'models': {
            'eccentricity': {
                'offset': [float(job.ecc_x or 0.0), float(job.ecc_y or 0.0), float(job.ecc_z or 0.0)]
            }
        }
    }
    if job.receiver is not None:
        site_options['receiver_type'] = job.receiver
    if job.antenna is not None:
        site_options['antenna_type'] = job.antenna
    ginan_template['receiver_options'] = {site_code: site_options}

    pea_config_path = os.path.join(workdir, f'pea_config_{job.sitename}.yaml')
    with open(pea_config_path, 'w') as yaml_out:
        yaml.dump(ginan_template, yaml_out, default_flow_style=False)

    logger.info(f"Running Ginan for {job.sitename} (obs: {obs_file_workdir_path})")
    pea_result = subprocess.run([config.config["pea_path"], "-y", pea_config_path], capture_output=True, text=True)
    pea_log_path = os.path.join(_log_dir, f"pea_{job.sitename}_{config.time_of_data.strftime('%Y-%m-%d')}.log")
    with open(pea_log_path, "w") as f:
        f.write(pea_result.stdout)
        if pea_result.stderr:
            f.write("\n--- stderr ---\n")
            f.write(pea_result.stderr)
    logger.info(f"Ginan exited with code {pea_result.returncode} for {job.sitename} (log: {pea_log_path})")
    if pea_result.returncode != 0:
        logger.error(f"Ginan failed for {job.sitename}, see {pea_log_path}")

    tree = ET.parse(os.path.join(workdir, gpx_filename))
    root = tree.getroot()

    x  = float(root[1][1][-1][2][1][0].text)
    y  = float(root[1][1][-1][2][1][1].text)
    z  = float(root[1][1][-1][2][1][2].text)
    dx = float(root[1][1][-1][2][2][0].text)
    dy = float(root[1][1][-1][2][2][1].text)
    dz = float(root[1][1][-1][2][2][2].text)
    logger.debug(f"{job.sitename}: raw ITRF2020 ECEF from GPX: x={x}, y={y}, z={z}, dx={dx}, dy={dy}, dz={dz}")

    raw_x, raw_y, raw_z = x, y, z

    # ITRF2020 ECEF -> target CRS from site_coordinates
    if job.target_crs_epsg is not None:
        decimal_year = config.time_of_data.year + (config.time_of_data.timetuple().tm_yday - 1) / 365.25
        x, y, z, _ = Transformer.from_crs("EPSG:9988", f"EPSG:{job.target_crs_epsg}", always_xy=True).transform(x, y, z, tt=decimal_year)
        logger.debug(f"{job.sitename}: after ITRF2020->EPSG:{job.target_crs_epsg} transform: x={x}, y={y}, z={z}")

    # target ECEF -> geographic (lat/lon/h_e) on GRS80
    lon, lat, h_e = Transformer.from_pipeline(
        "+proj=cart +ellps=GRS80 +inv"
    ).transform(x, y, z)

    # target ECEF -> ENU relative to reference position in site_coordinates
    if job.ref_x is not None and job.ref_y is not None and job.ref_z is not None:
        e, n, u = Transformer.from_pipeline(
            f"+proj=topocentric +X_0={job.ref_x} +Y_0={job.ref_y} +Z_0={job.ref_z} +ellps=GRS80"
        ).transform(x, y, z)
    else:
        e = n = u = None

    logger.info(f"{job.sitename} {config.time_of_data}: xyz=({x}, {y}, {z}) lat={lat} lon={lon} h_e={h_e} enu=({e}, {n}, {u})")

    with psycopg2.connect(
        host=os.environ.get("DB_HOST", "postgres"),
        port=os.environ.get("DB_PORT", 5432),
        dbname=os.environ.get("DB_NAME", "postgres"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", "password"),
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO position
                    (sitename, raw_x, raw_y, raw_z, x, y, z, dx, dy, dz, lat, lon, h_e, e, n, u,
                     time_of_data, time_of_calc)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                     %s, %s)
                """,
                (job.sitename, raw_x, raw_y, raw_z, x, y, z, dx, dy, dz, lat, lon, h_e, e, n, u,
                 config.time_of_data, datetime.datetime.now(datetime.timezone.utc)),
            )
        logger.info(f"DB write successful for {job.sitename} {config.time_of_data}")


def unpack(filepath: str):
    root, ext = os.path.splitext(filepath)
    if ext.lower() == ".gz":
        subprocess.run(["gzip", "-f", "-d", filepath])
        filepath = root

    root, ext = os.path.splitext(filepath)
    if ext.lower() == ".crx":
        subprocess.run(["/usr/local/bin/crx2rnx", "-f", "-d", filepath])
        filepath = root + ".rnx"
    
    return filepath


parser = argparse.ArgumentParser()
parser.add_argument("--from-days-back", type=int, default=2,
                    help="First day to process, as days ago (default: 2)")
parser.add_argument("--to-days-back", type=int, default=None,
                    help="Last day to process, as days ago, inclusive (default: same as --from-days-back)")
parser.add_argument("--station", nargs="+", metavar="PATTERN",
                    help="Only process stations matching these patterns (supports wildcards, e.g. 'ABC*')")
parser.add_argument("--skip-existing", action="store_true",
                    help="Skip site/day combinations that already have a result in the database")
args = parser.parse_args()
if args.to_days_back is None:
    args.to_days_back = args.from_days_back

autoppp_directory = os.path.dirname(__file__)
_start_time = datetime.datetime.now(datetime.timezone.utc)

today = datetime.datetime.now(datetime.timezone.utc).date()
date_from = today - datetime.timedelta(days=args.from_days_back)
date_to   = today - datetime.timedelta(days=args.to_days_back)
n_days = args.to_days_back - args.from_days_back + 1
plan_parts = [f"date range {date_from} to {date_to} ({n_days} day(s))"]
if args.station:
    plan_parts.append(f"station filter: {args.station}")
if args.skip_existing:
    plan_parts.append("skip existing: yes")
logger.info(f"autoppp_ginan starting — {', '.join(plan_parts)}")

# Phase 1: prepare each day sequentially (FTP downloads, DB queries)
day_runs = []  # list of (config, workdir, product_path_dict, jobs)
for days_back in range(args.from_days_back, args.to_days_back + 1):
    time_of_data = datetime.datetime.combine(
        datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=days_back),
        datetime.time.min,
        tzinfo=datetime.timezone.utc
    )
    logger.info(f"Processing date {time_of_data.strftime('%Y-%m-%d')} (GPS week {str(int((time_of_data - GPS_START).total_seconds() / SECONDS_OF_WEEK))}, DOY {time_of_data.strftime('%j')})")

    config = Config(time_of_data)

    workdir = os.path.join(autoppp_directory, config.config["output_directory"],
                           f"{config.year}_{config.doy}")
    os.makedirs(workdir, exist_ok=True)

    product_path_dict = {}
    for ftp_server in config.config["ftp_servers"]:
        try:
            with FTP(ftp_server["host"]) as ftp:
                ftp.login()
                ftp.cwd(ftp_server["remote_folder"])
                for product, file in ftp_server["rapid"].items():
                    local_product_file_path = os.path.join(workdir, file)
                    logger.info(f"Downloading {product} from {ftp_server['host']}... ({file})")
                    with open(local_product_file_path, "wb") as local_product_file:
                        ftp.retrbinary(f"RETR {file}", local_product_file.write)
                    product_path_dict[product] = unpack(local_product_file_path)
            break
        except OSError as e:
            logger.warning(f"FTP download from {ftp_server['host']} failed: {e}, trying next server...")
    else:
        logger.error(f"All FTP servers failed for {time_of_data.strftime('%Y-%m-%d')}, skipping day")
        continue

    with psycopg2.connect(
        host=os.environ.get("DB_HOST", "postgres"),
        port=os.environ.get("DB_PORT", 5432),
        dbname=os.environ.get("DB_NAME", "postgres"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", "password"),
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT sitename, target_crs_epsg, x, y, z,
                       receiver, antenna, ecc_x, ecc_y, ecc_z
                FROM site_metadata
                WHERE valid_from <= %s
                  AND (valid_to IS NULL OR valid_to > %s)
            """, (time_of_data, time_of_data))
            site_rows = cur.fetchall()

    if args.station:
        site_rows = [row for row in site_rows
                     if any(fnmatch.fnmatch(row[0].upper(), p.upper()) for p in args.station)]

    if args.skip_existing:
        with psycopg2.connect(
            host=os.environ.get("DB_HOST", "postgres"),
            port=os.environ.get("DB_PORT", 5432),
            dbname=os.environ.get("DB_NAME", "postgres"),
            user=os.environ.get("DB_USER", "postgres"),
            password=os.environ.get("DB_PASSWORD", "password"),
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT sitename FROM position WHERE time_of_data = %s",
                    (time_of_data,),
                )
                done_sites = {row[0] for row in cur.fetchall()}
        site_rows = [row for row in site_rows if row[0] not in done_sites]
        if done_sites:
            logger.info(f"Skipping {len(done_sites)} site(s) already in database for {time_of_data.strftime('%Y-%m-%d')}")

    jobs = [SiteJob.from_site_row(row, config) for row in site_rows]
    logger.info(f"{len(jobs)} sites to process: {[j.sitename for j in jobs]}")
    day_runs.append((config, workdir, product_path_dict, jobs))

# Phase 2: process all jobs across all days in one pool
ginan_instances = day_runs[0][0].config["ginan_instances"] if day_runs else 1
total_jobs = sum(len(jobs) for _, _, _, jobs in day_runs)
logger.info(f"Submitting {total_jobs} job(s) across {len(day_runs)} day(s) with {ginan_instances} worker(s)")
all_futures = {}
with concurrent.futures.ThreadPoolExecutor(max_workers=ginan_instances) as executor:
    for config, workdir, product_path_dict, jobs in day_runs:
        for job in jobs:
            f = executor.submit(process_obs_file, job, config, workdir, product_path_dict, autoppp_directory)
            all_futures[f] = job

n_ok, n_fail = 0, 0
for future, job in all_futures.items():
    try:
        future.result()
        n_ok += 1
    except FileNotFoundError as e:
        logger.warning(f"Skipping {job.sitename}: {e}")
        n_fail += 1
    except Exception:
        logger.exception(f"Error processing {job.obs_file}")
        n_fail += 1
logger.info(f"{n_ok} succeeded, {n_fail} failed")

# Phase 3: clean up each day's workdir
for _, workdir, _, _ in day_runs:
    shutil.rmtree(workdir)
    logger.info(f"Workdir cleaned up: {workdir}")

elapsed = datetime.datetime.now(datetime.timezone.utc) - _start_time
logger.info(f"autoppp_ginan finished in {elapsed}")

