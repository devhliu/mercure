"""
router.py
=========
mercure's central router module that evaluates the routing rules and decides which series should be sent to which target.
"""

# Standard python includes
import asyncio
import os
import signal
import sys
import time
import typing
from dataclasses import dataclass, field
from typing import Dict

import common.config as config
import common.helper as helper
import common.influxdb
import common.monitor as monitor
import common.notification as notification
import graphyte
import hupper
# App-specific includes
from common.constants import mercure_defs
from routing.common import SeriesItem, generate_task_id
from routing.route_series import route_error_files, route_series
from routing.route_studies import route_studies


@dataclass
class RouterState():
    filecount = 0
    series: Dict[str, SeriesItem] = field(default_factory=dict)
    complete_series: typing.Set[str] = field(default_factory=set)
    pending_series: Dict[str, float] = field(default_factory=dict)  # Every series that hasn't timed out yet


# Create local logger instance
logger = config.get_logger()
main_loop = None  # type: helper.AsyncTimer  # type: ignore


async def terminate_process(signalNumber, frame) -> None:
    """
    Triggers the shutdown of the service
    """
    helper.g_log("events.shutdown", 1)
    logger.info("Shutdown requested")
    monitor.send_event(monitor.m_events.SHUTDOWN_REQUEST, monitor.severity.INFO)
    # Note: main_loop can be read here because it has been declared as global variable
    if "main_loop" in globals() and main_loop.is_running:
        main_loop.stop()
    helper.trigger_terminate()


def run_router() -> None:
    """
    Main processing function that is called every second
    """
    if helper.is_terminated():
        return

    helper.g_log("events.run", 1)
    # logger.info('')
    # logger.info('Processing incoming folder...')
    logger.debug('Running router')
    try:
        config.read_config()
    except Exception:
        logger.warning(  # handle_error
            "Unable to update configuration. Skipping processing.",
            None,
            event_type=monitor.m_events.CONFIG_UPDATE,
        )
        return

    r = RouterState()
    error_files_found = False
    for entry in os.scandir(config.mercure.incoming_folder):
        if entry.name.endswith('.error'):
            error_files_found = True
            continue
        if not entry.is_dir():
            continue
        if entry.name == "error":
            error_files_found = True
            continue
        # if not entry.name.endswith(".received"):
        #     continue
        series_uid = entry.name
        mtime = entry.stat().st_mtime
        if series_uid not in r.series:
            r.series[series_uid] = SeriesItem(mtime)
        elif mtime > r.series[series_uid].modification_time:
            r.series[series_uid].modification_time = mtime

    # Check if any of the series exceeds the "series complete" threshold
    for series_uid, series_item in r.series.items():
        if (time.time() - series_item.modification_time) > config.mercure.series_complete_trigger:
            r.complete_series.add(series_uid)
            logger.debug("Complete series: " + str(series_uid))
        else:
            r.pending_series[series_uid] = series_item.modification_time
            logger.debug("Pending series: " + str(series_uid))

    # logger.info(f'Files found     = {filecount}')
    # logger.info(f'Series found    = {len(series)}')
    # logger.info(f'Complete series = {len(complete_series)}')
    helper.g_log("incoming.files", r.filecount)
    helper.g_log("incoming.series", len(r.series))

    # Process all complete series
    for series_uid in sorted(r.complete_series):
        task_id = generate_task_id()
        try:
            route_series(task_id, series_uid)
            del r.series[series_uid]
            r.complete_series.remove(series_uid)
        except Exception:
            logger.error(f"Problems while routing series {series_uid}", task_id)  # handle_error
        # If termination is requested, stop processing series after the active one has been completed
        if helper.is_terminated():
            return

    if error_files_found:
        logger.warning("Error files found during routing")
        route_error_files()

    # Now, check if studies in the studies folder are ready for routing/processing
    route_studies(r.pending_series)


def exit_router(args) -> None:
    """
    Callback function that is triggered when the process terminates. Stops the asyncio event loop
    """
    helper.loop.call_soon_threadsafe(helper.loop.stop)


# Main entry point of the router module
def main(args=sys.argv[1:]) -> None:
    if "--reload" in args or os.getenv("MERCURE_ENV", "PROD").lower() == "dev":
        # start_reloader will only return in a monitored subprocess
        _ = hupper.start_reloader("router.main")

    logger.info("")
    logger.info(f"mercure DICOM Router ver {mercure_defs.VERSION}")
    logger.info("--------------------------------------------")
    logger.info("")

    # Register system signals to be caught
    signals = (signal.SIGTERM, signal.SIGINT)
    for s in signals:
        helper.loop.add_signal_handler(s, lambda s=s: asyncio.create_task(terminate_process(s, helper.loop)))

    instance_name = "main"

    # Read the optional instance name from the argument (if running multiple instances in one appliance)
    if len(sys.argv) > 1:
        instance_name = sys.argv[1]

    # Read the configuration file and terminate if it cannot be read
    try:
        config.read_config()
    except Exception:
        logger.exception("Cannot start service. Going down.")
        sys.exit(1)

    appliance_name = config.mercure.appliance_name

    logger.info(f"Appliance name = {appliance_name}")
    logger.info(f"Instance  name = {instance_name}")
    logger.info(f"Instance  PID  = {os.getpid()}")
    logger.info(sys.version)

    notification.setup()
    monitor.configure("router", instance_name, config.mercure.bookkeeper)
    monitor.send_event(monitor.m_events.BOOT, monitor.severity.INFO, f"PID = {os.getpid()}")

    if len(config.mercure.graphite_ip) > 0:
        logger.info(f"Sending events to graphite server: {config.mercure.graphite_ip}")
        graphite_prefix = "mercure." + appliance_name + ".router." + instance_name
        graphyte.init(config.mercure.graphite_ip, config.mercure.graphite_port, prefix=graphite_prefix)

    if len(config.mercure.influxdb_host) > 0:
        logger.info(f"Sending events to influxdb server: {config.mercure.influxdb_host}")
        common.influxdb.init(
            config.mercure.influxdb_host,
            config.mercure.influxdb_token,
            config.mercure.influxdb_org,
            config.mercure.influxdb_bucket,
            "mercure." + appliance_name + ".router." + instance_name
        )

    logger.info(
        f"""Incoming folder: {config.mercure.incoming_folder}
        Studies folder: {config.mercure.studies_folder}
        Outgoing folder: {config.mercure.outgoing_folder}
        Processing folder: {config.mercure.processing_folder}"""
    )

    # Start the timer that will periodically trigger the scan of the incoming folder
    global main_loop
    main_loop = helper.AsyncTimer(config.mercure.router_scan_interval, run_router)

    helper.g_log("events.boot", 1)

    try:
        main_loop.run_until_complete(helper.loop)
        # Process will exit here once the asyncio loop has been stopped
        monitor.send_event(monitor.m_events.SHUTDOWN, monitor.severity.INFO)
    except Exception as e:
        monitor.send_event(monitor.m_events.SHUTDOWN, monitor.severity.ERROR, str(e))
        logger.exception(e)
    finally:
        # Finish all asyncio tasks that might be still pending
        remaining_tasks = helper.asyncio.all_tasks(helper.loop)  # type: ignore[attr-defined]
        if remaining_tasks:
            helper.loop.run_until_complete(helper.asyncio.gather(*remaining_tasks))

    logger.info("Going down now")
