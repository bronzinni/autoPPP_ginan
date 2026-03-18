import argparse
import concurrent.futures
import json
import datetime
import time

import os
import subprocess

from ftplib import FTP

import yaml

import psycopg2

from pyproj import Transformer


SECONDS_OF_WEEK = 7*24*60*60
GPS_START = datetime.datetime(year=1980, month=1, day=6)

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
    def replace_placeholders(self, string: str, sitename: str = ""):
        return (string.replace("~WEEK~", self.week)
                      .replace("~YEAR~", self.year)
                      .replace("~DOY~", self.doy)
                      .replace("~SITENAME~", sitename))


def process_obs_file(obs_file, config, workdir, product_path_dict, autoppp_directory):
    import xml.etree.ElementTree as ET

    sitename = os.path.basename(obs_file)[:9]

    obs_file_storage_path = os.path.join(autoppp_directory, obs_file)
    obs_file_workdir_path = os.path.join(workdir, os.path.basename(obs_file))

    subprocess.run(["cp", obs_file_storage_path, obs_file_workdir_path])

    obs_file_workdir_path = unpack(obs_file_workdir_path)

    ginan_template_path = os.path.join(autoppp_directory, "ginan_template.yaml")
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

    ginan_template['inputs']['erp_files'] = [product_path_dict["ERP"]]
    ginan_template['inputs']['satellite_data']['clk_files'] = [product_path_dict["CLK"]]
    ginan_template['inputs']['satellite_data']['bsx_files'] = [product_path_dict["BIA"], product_path_dict["OBX"]]
    ginan_template['inputs']['satellite_data']['sp3_files'] = [product_path_dict["SP3"]]

    pea_config_path = os.path.join(workdir, f'pea_config_{sitename}.yaml')
    with open(pea_config_path, 'w') as yaml_out:
        yaml.dump(ginan_template, yaml_out, default_flow_style=False)

    print(f"Running Ginan for {sitename}")
    subprocess.run([config.config["pea_path"], "-y", pea_config_path], capture_output=True)

    print(os.path.join(workdir, f"{sitename}_autoppp_ginan_{config.year}{config.doy.rjust(3,'0')}00.GPX"))

    tree = ET.parse(os.path.join(workdir, f"{sitename}_autoppp_ginan_{config.year}{config.doy.rjust(3,'0')}00.GPX"))
    root = tree.getroot()

    x  = float(root[1][1][-1][2][1][0].text)
    y  = float(root[1][1][-1][2][1][1].text)
    z  = float(root[1][1][-1][2][1][2].text)
    dx = float(root[1][1][-1][2][2][0].text)
    dy = float(root[1][1][-1][2][2][1].text)
    dz = float(root[1][1][-1][2][2][2].text)

    # ITRF2020 ECEF (EPSG:9988) -> ETRS89 geographic 3D (EPSG:4937)
    # PROJ selects EPSG:10895 (ITRF2020 to ETRS89) automatically
    itrf2020_to_etrs89 = Transformer.from_crs("EPSG:9988", "EPSG:4937", always_xy=True)
    lon, lat, h_e = itrf2020_to_etrs89.transform(x, y, z)

    # ETRS89 geographic -> ETRS89/UTM, zone restricted to {24, 29, 32}
    allowed_zones = [24, 29, 32]
    raw_zone = int((lon + 180) / 6) + 1
    utm_zone_number = min(allowed_zones, key=lambda z: abs(z - raw_zone))
    utm_epsg = {24: 25824, 29: 25829, 32: 25832}
    etrs89_to_utm = Transformer.from_crs("EPSG:4937", f"EPSG:{utm_epsg[utm_zone_number]}", always_xy=True)
    easting, northing = etrs89_to_utm.transform(lon, lat)
    utm_zone = f"{utm_zone_number}N"

    print(config.time_of_data, sitename,
          x, y, z, dx, dy, dz,
          lat, lon, h_e,
          utm_zone, easting, northing)

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
                    (sitename, x, y, z, dx, dy, dz, lat, lon, h_e,
                     utm_zone, easting, northing, time_of_data, time_of_calc)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                     %s, %s, %s, %s, %s)
                ON CONFLICT (sitename, time_of_data) DO UPDATE SET
                    x = EXCLUDED.x,
                    y = EXCLUDED.y,
                    z = EXCLUDED.z,
                    dx = EXCLUDED.dx,
                    dy = EXCLUDED.dy,
                    dz = EXCLUDED.dz,
                    lat = EXCLUDED.lat,
                    lon = EXCLUDED.lon,
                    h_e = EXCLUDED.h_e,
                    utm_zone = EXCLUDED.utm_zone,
                    easting = EXCLUDED.easting,
                    northing = EXCLUDED.northing,
                    time_of_calc = EXCLUDED.time_of_calc
                """,
                (sitename, x, y, z, dx, dy, dz, lat, lon, h_e,
                 utm_zone, easting, northing,
                 config.time_of_data, datetime.datetime.now()),
            )


def unpack(filepath: str):
    root, ext = os.path.splitext(filepath)
    if ext.lower() == ".gz":
        subprocess.run(["gzip", "-f", "-d", filepath])
        filepath = root

    root, ext = os.path.splitext(filepath)
    if ext.lower() == ".crx":
        subprocess.run(["crx2rnx", "-f", "-d", filepath])
        filepath = root + ".rnx"
    
    return filepath


parser = argparse.ArgumentParser()
parser.add_argument("--days-back", type=int, default=2, help="Process from 2 days ago back to this many days ago (default: 2)")
args = parser.parse_args()

for days_back in range(2, args.days_back + 1):
    time_of_data = datetime.datetime.now() - datetime.timedelta(days=days_back)

    config = Config(time_of_data)

    autoppp_directory = os.path.dirname(__file__)

    workdir = os.path.join(autoppp_directory, config.config["output_directory"])


    # download PPP products
    product_path_dict = {}
    # should loop through config.config
    with FTP(config.config["ftp_servers"][0]["host"]) as ftp:
        ftp.login()
        ftp.cwd(config.config["ftp_servers"][0]["remote_folder"])
        for product, file in config.config["ftp_servers"][0]["files"].items():
            local_product_file_path = os.path.join(workdir, file)
            print(f"Downloading {product}...")
            with open(local_product_file_path, "wb") as local_product_file:
                ftp.retrbinary(f"RETR {file}", local_product_file.write)
            product_path_dict[product] = unpack(local_product_file_path)
 
    with psycopg2.connect(
        host=os.environ.get("DB_HOST", "postgres"),
        port=os.environ.get("DB_PORT", 5432),
        dbname=os.environ.get("DB_NAME", "postgres"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", "password"),
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT sitename FROM sites")
            sitenames = [row[0] for row in cur.fetchall()]

    observation_files = [
        config.replace_placeholders(config.config["observation_file_template"], sitename)
        for sitename in sitenames
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=config.config["ginan_instances"]) as executor:
        for obs_file in observation_files:
            executor.submit(process_obs_file, obs_file, config, workdir, product_path_dict, autoppp_directory)
    

# gps_test = datetime.datetime(year=2021, month=12, day=29)

# two_days_ago = datetime.datetime.now() - datetime.timedelta(days=2)

# print(int((gps_test-gps_start).total_seconds()/SECONDS_OF_WEEK))

# print(two_days_ago.strftime("%Y/%j"))

