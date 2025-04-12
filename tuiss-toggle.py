#!/usr/bin/env python3

import asyncio
from bleak import BleakClient, BleakError, BleakScanner
import sys
import logging
import argparse # For command-line arguments

# --- Configuration ---
# Make sure this is the correct address for your blind on macOS
BLIND_ADDRESS = "70B5DC24-7A0C-38C6-B482-B5B12BE74764"
# UUIDs derived from Tuiss2HA project
WRITE_UUID = "00010405-0405-0607-0809-0a0b0c0d1910"
# NOTIFY_UUID is no longer needed as we don't read position

# --- Commands (as byte arrays) ---
# Derived from Tuiss2HA hub.py
KEEP_ALIVE_COMMAND = bytes.fromhex("ff03030303787878787878")
# GET_POSITION_COMMAND is no longer needed
# hex_convert(0) -> ff78ea41bf03e803 (Set position to 100% closed)
CLOSE_COMMAND = bytes.fromhex("ff78ea41bf03e803")
# hex_convert(100) -> ff78ea41bf030000 (Set position to 0% closed / 100% open)
OPEN_COMMAND = bytes.fromhex("ff78ea41bf030000")

# --- Logging Setup ---
# Set level to DEBUG for more detailed output, including BLE writes
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("TuissControl")


# --- Scan Function ---
async def scan_for_devices(scan_duration=5.0):
    """Scans for BLE devices for a specified duration."""
    log.info(f"Scanning for BLE devices for {scan_duration} seconds...")
    try:
        devices = await BleakScanner.discover(timeout=scan_duration)
        if not devices:
            log.info("No BLE devices found nearby.")
            return

        log.info(f"Found {len(devices)} devices:")
        for device in devices:
            # Attempt to decode name, handle potential decoding errors
            try:
                # Use device.details to get more info if needed, especially on macOS
                device_name = device.name if device.name else "Unknown"
            except UnicodeDecodeError:
                device_name = "Unknown (decode error)"

            # device.address is UUID on macOS, standard MAC on Linux/Windows
            log.info(f"  Address: {device.address}, Name: {device_name}")

    except BleakError as e:
        log.error(f"Bluetooth scanning error: {e}")
    except Exception as e:
        log.error(f"An unexpected error occurred during scanning: {e}")


# --- Send Command Function ---
async def send_blind_command(command_to_send, command_name):
    """Connects to the blind and sends a specific command."""
    client = None # Initialize client to None

    log.info(f"Attempting to connect to blind {BLIND_ADDRESS} to send '{command_name}' command...")
    try:
        # Use a context manager for reliable connection/disconnection
        async with BleakClient(BLIND_ADDRESS, timeout=20.0) as client: # Increased timeout
            if not client.is_connected:
                log.error("Failed to connect (unexpected state).")
                return

            log.info("Connected successfully. MTU: %s", client.mtu_size) # Log MTU size

            # 1. Send Keep Alive
            log.debug(f"Writing Keep Alive command ({KEEP_ALIVE_COMMAND.hex()}) to {WRITE_UUID}...")
            await client.write_gatt_char(WRITE_UUID, KEEP_ALIVE_COMMAND, response=False)
            log.info("Keep-alive sent.")
            await asyncio.sleep(0.5) # Short delay seems beneficial

            # 2. Send the Target Command
            log.debug(f"Writing {command_name} command ({command_to_send.hex()}) to {WRITE_UUID} with response=True...")
            # *** Try writing WITH response ***
            await client.write_gatt_char(WRITE_UUID, command_to_send, response=True)
            log.info(f"{command_name} command sent and acknowledged (response=True).")
            # *** Increase delay after sending command ***
            log.info("Waiting 3 seconds for blind to process command...")
            await asyncio.sleep(3.0)

            log.info("Operation complete.")

    except BleakError as e:
        log.error(f"Bluetooth Error during operation: {e}")
        log.error("Ensure Bluetooth is on, the blind is powered and in range, and the MAC address is correct.")
        if "response=True" in str(e):
             log.error("The device might not support 'Write With Response'. Consider changing back to response=False.")
    except Exception as e:
        log.error(f"An unexpected error occurred: {e}")
    finally:
        # The 'async with' statement handles disconnection automatically.
        log.debug(f"Send command function finished for '{command_name}'.")


# --- Run the script ---
if __name__ == "__main__":
    # Setup argument parser
    parser = argparse.ArgumentParser(
        description="Control Tuiss BLE Blinds.",
        epilog="Requires the 'bleak' Python library (pip install bleak). "
               "Ensure the BLIND_ADDRESS constant in the script matches your blind's address "
               "(use --scan to find it). The script defaults to '--open' if no action is specified."
    )
    # Scan arguments
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan for nearby BLE devices instead of controlling the blind."
    )
    parser.add_argument(
        "--scantime",
        type=float,
        default=5.0,
        help="Duration in seconds for the BLE scan (default: 5.0)."
    )

    # Mutually exclusive group for open/close actions
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument(
        "--open",
        action="store_true",
        help="Send the command to fully open the blinds."
    )
    action_group.add_argument(
        "--close",
        action="store_true",
        help="Send the command to fully close the blinds."
    )

    args = parser.parse_args()

    # Check platform
    if sys.platform != "darwin":
         log.warning("This script is primarily tested on macOS. Running on other OS may require adjustments.")

    # Decide whether to scan or send a command
    if args.scan:
        asyncio.run(scan_for_devices(scan_duration=args.scantime))
    else:
        # Determine the command based on arguments (default to OPEN)
        if args.close:
            target_command = CLOSE_COMMAND
            command_name = "CLOSE"
        else: # Default action is OPEN (or if --open is explicitly specified)
            target_command = OPEN_COMMAND
            command_name = "OPEN"

        # Run the command sending function
        asyncio.run(send_blind_command(target_command, command_name))

    log.info("Script execution finished.")
"""
**Key Changes:**

1.  **Logging Level:** Set to `DEBUG` to show more detailed logs, including the hex values of commands being written.
2.  **Write With Response:** Changed `await client.write_gatt_char(WRITE_UUID, command_to_send, response=False)` to `response=True`. If this causes a new error, it means the device doesn't support Write With Response, and we might need to revert this specific change.
3.  **Increased Delay:** Changed `asyncio.sleep(1.0)` after sending the command to `asyncio.sleep(3.0)`.

Please try running the script again (e.g., `python your_script_name.py --close`). Observe the output carefully (especially the DEBUG messages) and see if the blind moves this time. If it still doesn't work or you get a new error related to `response=True`, please share the output.

Also, consider trying to operate the blind once using the official Tuiss SmartView app if you haven't recently, just in case it needs that activation/sync st"""