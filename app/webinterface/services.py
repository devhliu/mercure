"""
services.py
===========
Helper functions for controlling the services from the graphical user interface of mercure.
"""

# Standard python includes
import json
import os
from pathlib import Path
import daiquiri

# App-specific includes
from common import config

# Create local logger instance
logger = config.get_logger()

services_filename = (os.getenv("MERCURE_CONFIG_FOLDER") or "/opt/mercure/config") + "/services.json"
services_list = {}


def read_services() -> None:
    """Reads the list of configured services from the configuration file. This list normally does not have to be changed, but
    can be modified if multiple instances of individual services should be used to increase performance."""
    global services_list
    services_file = Path(services_filename)

    if not services_file.exists():
        raise FileNotFoundError(f"Services file not found: {services_file}")

    logger.info(f"Reading services from: {services_file}")

    with open(services_file, "r") as json_file:
        services_list = json.load(json_file)
