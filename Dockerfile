FROM gnssanalysis/ginan:v4.1.1

WORKDIR /autoppp_ginan

RUN apt-get update && apt-get install -y cron logrotate \
    && apt-get clean

RUN curl -L https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh -o /tmp/miniforge.sh \
    && bash /tmp/miniforge.sh -b -p /opt/conda \
    && rm /tmp/miniforge.sh \
    && /opt/conda/bin/mamba clean -afy

COPY environment.yaml .
RUN /opt/conda/bin/mamba env create -f environment.yaml \
    && /opt/conda/bin/mamba clean -afy

ENV PROJ_LOG_LEVEL=ERROR

# Sync PROJ deformation grids into pyproj's own data directory so they are
# always found without needing PROJ_LIB to be set in the environment.
RUN PROJ_DIR=$(/opt/conda/bin/conda run -n autoppp_ginan python -c "import pyproj; print(pyproj.datadir.get_data_dir())") \
    && /opt/conda/bin/conda run -n autoppp_ginan pyproj sync --source-id dk_sdfe --target-dir "$PROJ_DIR" \
    && /opt/conda/bin/conda run -n autoppp_ginan pyproj sync --source-id dk_sdfi --target-dir "$PROJ_DIR" \
    && /opt/conda/bin/conda run -n autoppp_ginan pyproj sync --source-id dk_kds  --target-dir "$PROJ_DIR" \
    && /opt/conda/bin/conda run -n autoppp_ginan pyproj sync --source-id eur_nkg --target-dir "$PROJ_DIR"

RUN /opt/conda/bin/conda init bash \
    && echo "conda activate autoppp_ginan" >> /root/.bashrc

COPY autoppp_ginan.py .
COPY entrypoint.sh .
COPY bin/crx2rnx /usr/local/bin/crx2rnx


RUN chmod +x /usr/local/bin/crx2rnx \
    && mkdir -p workdir logs \
    && printf '0 0 * * * root logrotate /etc/logrotate.d/autoppp_ginan\n0 0 * * * root find /autoppp_ginan/logs -name "pea_*.log" -mtime +30 -delete\n0 1 * * * root . /etc/autoppp_env && cd /autoppp_ginan && /opt/conda/envs/autoppp_ginan/bin/python3 autoppp_ginan.py --from-days-back 2 --to-days-back 3 --skip-existing\n' > /etc/cron.d/autoppp_ginan \
    && chmod 0644 /etc/cron.d/autoppp_ginan \
    && chmod +x entrypoint.sh \
    && printf '/autoppp_ginan/logs/autoppp_ginan.log {\n    daily\n    rotate 30\n    compress\n    delaycompress\n    missingok\n    notifempty\n    copytruncate\n}\n' > /etc/logrotate.d/autoppp_ginan

CMD ["/autoppp_ginan/entrypoint.sh"]
