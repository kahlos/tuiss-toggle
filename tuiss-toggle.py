#!/usr/bin/env python3

import asyncio
from bleak import BleakClient, BleakError, BleakScanner # Added BleakScanner
import sys
import logging
import argparse # Added argparse for command-line arguments

# --- Configuration ---
# Replace with your blind's specific MAC address if different
BLIND_ADDRESS = "70B5DC24-7A0C-38C6-B482-B5B12BE74764"
# UUIDs derived from Tuiss2HA project
WRITE_UUID = "00010405-0405-0607-0809-0a0b0c0d1910"
NOTIFY_UUID = "00010304-0405-0607-0809-0a0b0c0d1910"

# --- Commands (as byte arrays) ---
# Derived from Tuiss2HA hub.py
KEEP_ALIVE_COMMAND = bytes.fromhex("ff03030303787878787878")
GET_POSITION_COMMAND = bytes.fromhex("ff78ea41d10301")
# hex_convert(0) -> ff78ea41bf03e803 (Set position to 100% closed)
CLOSE_COMMAND = bytes.fromhex("ff78ea41bf03e803")
# hex_convert(100) -> ff78ea41bf030000 (Set position to 0% closed / 100% open)
OPEN_COMMAND = bytes.fromhex("ff78ea41bf030000")

# --- Global variable to store position and event ---
# Stores position as percentage closed (0=open, 100=closed)
current_position_closed = -1
position_received_event = asyncio.Event()

# --- Logging Setup ---
# Set to logging.DEBUG for more detailed BLE communication logs
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("TuissToggle")


# --- Notification Handler ---
def notification_handler(sender: int, data: bytearray):
    """Handles incoming notifications from the blind."""
    global current_position_closed
    log.debug(f"Received notification (handle {sender}): {data.hex()}")

    # Parse position based on Tuiss2HA logic
    # Assumes bytes 7 and 8 contain the position data (0-1000 range)
    # Note: This parsing might need adjustment if the blind sends other notifications.
    # A more robust implementation would check header bytes (e.g., data[4] == 0xd1 for position)
    if len(data) >= 9: # Need at least 9 bytes for indices 7 and 8
        try:
            # Extract the two bytes representing position (little-endian)
            pos_byte_7 = data[7]
            pos_byte_8 = data[8]
            # Calculate position (0=open, 100=closed)
            calculated_pos = (pos_byte_7 + (256 * pos_byte_8)) / 10.0

            # Basic validation - position should be between 0 and 100
            if 0 <= calculated_pos <= 100:
                current_position_closed = calculated_pos
                log.info(f"Parsed position (0=open, 100=closed): {current_position_closed:.1f}%")
                position_received_event.set() # Signal that valid position was received
            else:
                log.warning(f"Parsed position {calculated_pos:.1f} out of range (0-100). Ignoring.")

        except IndexError:
            log.error("Received data too short to parse position at expected indices.")
        except Exception as e:
            log.error(f"Error parsing position data: {e}")
    else:
        # Log other short notifications but don't treat them as position updates
        log.debug("Received data too short, likely not position info.")


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
                device_name = device.name if device.name else "Unknown"
            except UnicodeDecodeError:
                device_name = "Unknown (decode error)"

            log.info(f"  Address: {device.address}, Name: {device_name}")

    except BleakError as e:
        log.error(f"Bluetooth scanning error: {e}")
    except Exception as e:
        log.error(f"An unexpected error occurred during scanning: {e}")


# --- Toggle Function ---
async def toggle_blinds():
    """Connects to the blind, gets position, and sends open/close command."""
    global current_position_closed
    client = None # Initialize client to None

    log.info(f"Attempting to connect to blind {BLIND_ADDRESS}...")
    try:
        # Use a context manager for reliable connection/disconnection
        async with BleakClient(BLIND_ADDRESS, timeout=20.0) as client: # Increased timeout
            if not client.is_connected:
                # This state should ideally not be reached within the context manager
                # if connection fails, it raises an exception handled below.
                log.error("Failed to connect (unexpected state).")
                return

            log.info("Connected successfully.")

            # 1. Send Keep Alive
            log.info("Sending keep-alive...")
            await client.write_gatt_char(WRITE_UUID, KEEP_ALIVE_COMMAND, response=False)
            await asyncio.sleep(0.5) # Short delay seems beneficial

            # 2. Get Current Position
            log.info("Requesting current position...")
            position_received_event.clear() # Reset event for this attempt
            current_position_closed = -1 # Reset position state

            # Start notifications *before* sending the command
            await client.start_notify(NOTIFY_UUID, notification_handler)
            log.debug("Notifications started.")
            await client.write_gatt_char(WRITE_UUID, GET_POSITION_COMMAND, response=False)
            log.debug("Get position command sent.")

            # Wait for position data with a timeout
            try:
                log.info("Waiting for position notification (up to 15 seconds)...")
                await asyncio.wait_for(position_received_event.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                log.error("Timed out waiting for position data.")
                # Attempt to stop notifications even on timeout
                try:
                    if client.is_connected: # Check connection before stopping notify
                       await client.stop_notify(NOTIFY_UUID)
                       log.debug("Stopped notifications after timeout.")
                except BleakError as e:
                    log.warning(f"Error stopping notifications after timeout: {e}")
                return # Exit if we can't get position
            finally:
                 # Ensure notifications are stopped if the event was set or timeout occurred
                 if client.is_connected:
                    try:
                        # Check if notifications are actually active before stopping
                        # (This part is tricky without direct state from bleak, rely on try/except)
                        await client.stop_notify(NOTIFY_UUID)
                        log.debug("Stopped notifications.")
                    except BleakError as e:
                         # Ignore error if already stopped or handle specific cases if needed
                         log.warning(f"Issue stopping notifications (might be expected): {e}")


            # Check if position was successfully parsed
            if current_position_closed < 0:
                log.error("Failed to determine current blind position after notification.")
                return

            # 3. Decide and Send Toggle Command
            # Determine threshold (e.g., 50%) to decide if open or closed
            toggle_threshold = 50.0
            if current_position_closed >= toggle_threshold: # If mostly closed (or exactly 50%), open it
                log.info(f"Blind is >= {toggle_threshold}% closed ({current_position_closed:.1f}%). Sending OPEN command...")
                target_command = OPEN_COMMAND
            else: # If mostly open, close it
                log.info(f"Blind is < {toggle_threshold}% closed ({current_position_closed:.1f}%). Sending CLOSE command...")
                target_command = CLOSE_COMMAND

            await client.write_gatt_char(WRITE_UUID, target_command, response=False)
            log.info("Toggle command sent.")
            await asyncio.sleep(1.0) # Wait a moment for command to likely be processed by blind

            log.info("Operation complete.")

    except BleakError as e:
        log.error(f"Bluetooth Error: {e}")
        log.error("Ensure Bluetooth is on, the blind is powered and in range, and the MAC address is correct.")
    except Exception as e:
        log.error(f"An unexpected error occurred: {e}")
    finally:
        # The 'async with' statement handles disconnection automatically.
        # No explicit disconnect needed here unless outside the 'async with'.
        log.debug("Toggle function finished.")


# --- Run the script ---
if __name__ == "__main__":
    # Setup argument parser
    parser = argparse.ArgumentParser(description="Toggle Tuiss BLE Blinds or Scan for devices.")
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan for nearby BLE devices instead of toggling the blind."
    )
    parser.add_argument(
        "--scantime",
        type=float,
        default=5.0,
        help="Duration in seconds for the BLE scan (default: 5.0)."
    )
    args = parser.parse_args()

    # Check platform
    if sys.platform != "darwin":
         log.warning("This script is primarily tested on macOS. Running on other OS may require adjustments.")

    # Decide whether to scan or toggle
    if args.scan:
        asyncio.run(scan_for_devices(scan_duration=args.scantime))
    else:
        asyncio.run(toggle_blinds())

    log.info("Script execution finished.")

"""
**How to use the new feature:**

1.  Save the updated code to your `toggle_blind.py` file.
2.  Open Terminal.
3.  Navigate to the directory where you saved the file.
4.  To **scan** for devices for 5 seconds (default):
    ```bash
    python toggle_blind.py --scan
    ```
5.  To scan for a different duration (e.g., 10 seconds):
    ```bash
    python toggle_blind.py --scan --scantime 10
    ```
6.  To **toggle** the blind (the original functionality):
    ```bash
    python toggle_blind.py
    ```

This should help you confirm if your Mac can see the blind's MAC address (`E1:1D:ED:42:D1:90`) and if the address is corre
```
"""