FROM gnssanalysis/ginan:v4.1.1

RUN apt-get update && apt-get install -y cron \
    && pip3 install psycopg2-binary pyyaml pyproj

WORKDIR /autoppp_ginan

COPY autoppp.py .
COPY entrypoint.sh .

RUN echo "0 1 * * * root . /etc/autoppp_env && cd /autoppp_ginan && python3 autoppp.py >> /var/log/autoppp.log 2>&1" > /etc/cron.d/autoppp \
    && chmod 0644 /etc/cron.d/autoppp \
    && chmod +x entrypoint.sh

CMD ["/autoppp_ginan/entrypoint.sh"]
