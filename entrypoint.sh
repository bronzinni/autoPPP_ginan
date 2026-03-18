#!/bin/bash

# Make Docker environment variables available to cron
printenv | grep -E "^DB_" > /etc/autoppp_env

cron -f
