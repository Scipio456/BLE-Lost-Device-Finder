import argparse
import asyncio
import csv
import json
import os
import statistics
import sys
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque, Iterable, List, Optional, Tuple

from bleak import BleakScanner
from dotenv import load_dotenv

# Try to import msvcrt for Windows-specific non-blocking keyboard input
try:
    import msvcrt
except ImportError:
    msvcrt = None


SAFETY_NOTICE = """
Bluetooth safety rules:
  - Use this only for earbuds or BLE devices that you own or have permission to track.
  - This tool keeps data local and does not upload anything.
  - Distance is only an estimate from signal strength, never an exact physical measurement.
  - Earbuds may stop advertising when inside their case, powered off, or connected in a vendor-specific way.
""".strip()


@dataclass
class Target:
    name: Optional[str] = None
    address: Optional[str] = None

    def __str__(self) -> str:
        if self.name and self.address:
            return f"{self.name} ({self.address})"
        return self.name or self.address or "Unknown"


class TargetManager:
    def __init__(self, targets: List[Target]) -> None:
        self.targets = targets
        self.active_index = 0

    @property
    def active_target(self) -> Optional[Target]:
        if not self.targets:
            return None
        return self.targets[self.active_index]

    def cycle(self) -> Target:
        if not self.targets:
            return None
        self.active_index = (self.active_index + 1) % len(self.targets)
        return self.active_target

    def matches(self, device_address: str, device_name: str) -> bool:
        for target in self.targets:
            if target.address and target.address.lower() == device_address.lower():
                return True
            if target.name and target.name.lower() in device_name.lower():
                return True
        return False

    def is_active(self, device_address: str, device_name: str) -> bool:
        active = self.active_target
        if not active:
            return False
        if active.address and active.address.lower() == device_address.lower():
            return True
        if active.name and active.name.lower() in device_name.lower():
            return True
        return False


@dataclass
class DeviceReading:
    timestamp: str
    address: str
    name: str
    rssi: int
    smoothed_rssi: float
    raw_distance_m: float
    estimated_distance_m: float
    estimated_distance_cm: float
    confidence: str
    proximity: str


class RssiWindow:
    def __init__(self, size: int) -> None:
        self._values: Deque[int] = deque(maxlen=size)

    def add(self, value: int) -> float:
        self._values.append(value)
        return sum(self._values) / len(self._values)

    def median(self) -> Optional[float]:
        if not self._values:
            return None
        return float(statistics.median(self._values))

    def robust_average(self) -> Optional[float]:
        if not self._values:
            return None
        ordered = sorted(self._values)
        if len(ordered) >= 5:
            ordered = ordered[1:-1]
        return float(sum(ordered) / len(ordered))

    def spread(self) -> Optional[int]:
        if len(self._values) < 2:
            return None
        return max(self._values) - min(self._values)

    def is_outlier(self, value: int, threshold: int = 10) -> bool:
        median = self.median()
        if median is None or len(self._values) < 4:
            return False
        return abs(value - median) > threshold

    def __len__(self) -> int:
        return len(self._values)


class DistanceFilter:
    def __init__(self) -> None:
        self._distance_m: Optional[float] = None

    def update(self, raw_distance_m: float, confidence: str) -> float:
        if self._distance_m is None:
            self._distance_m = raw_distance_m
            return self._distance_m

        alpha = 0.18
        if confidence == "high":
            alpha = 0.28
        elif confidence == "low":
            alpha = 0.10

        max_step = max(0.25, self._distance_m * 0.35)
        delta = raw_distance_m - self._distance_m
        if abs(delta) > max_step:
            delta = max_step if delta > 0 else -max_step

        self._distance_m += alpha * delta
        return self._distance_m


def parse_args() -> argparse.Namespace:
    try:
        load_dotenv()
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="Track nearby Bluetooth earbuds by RSSI and estimate distance."
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List nearby BLE devices and exit.",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="List nearby devices with raw BLE advertisement details and exit.",
    )
    parser.add_argument(
        "--choose",
        action="store_true",
        help="Scan nearby devices and choose one interactively before tracking.",
    )
    parser.add_argument(
        "--target-name",
        help="Track the first nearby device whose name contains this text. Supports comma-separated list.",
    )
    parser.add_argument(
        "--target-address",
        help="Track a specific BLE MAC/address exactly. Supports comma-separated list.",
    )
    parser.add_argument(
        "--scan-time",
        type=float,
        default=0.5,
        help="Seconds to scan during each refresh. Default: 0.5",
    )
    parser.add_argument(
        "--refresh-interval",
        type=float,
        default=0.05,
        help="Seconds to wait between scans. Default: 0.05",
    )
    parser.add_argument(
        "--window-size",
        type=int,
        default=5,
        help="RSSI smoothing window size. Default: 5",
    )
    parser.add_argument(
        "--reference-rssi",
        type=int,
        default=int(os.getenv("REFERENCE_RSSI", "-59")),
        help="Calibrated RSSI at 1 meter. Default: -59 dBm (or from .env)",
    )
    parser.add_argument(
        "--path-loss",
        type=float,
        default=float(os.getenv("PATH_LOSS_EXPONENT", "2.2")),
        help="Signal path-loss exponent. Default: 2.2 (or from .env)",
    )
    parser.add_argument(
        "--log",
        type=Path,
        help="Optional local CSV file for logging readings.",
    )
    parser.add_argument(
        "--max-missed",
        type=int,
        default=3,
        help="Warn after this many consecutive missed scans. Default: 3",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Use aggressive low-latency scanning settings.",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Print one JSON response for list or diagnose mode.",
    )
    parser.add_argument(
        "--json-lines",
        action="store_true",
        help="Print newline-delimited JSON events while tracking.",
    )
    args = parser.parse_args()

    # Load from environment if not provided via CLI
    if not args.target_address:
        args.target_address = os.getenv("TARGET_ADDRESSES")
    if not args.target_name:
        args.target_name = os.getenv("TARGET_NAME") or os.getenv("TARGET_NAMES")

    if args.window_size < 1:
        parser.error("--window-size must be at least 1")
    if args.scan_time <= 0 or args.refresh_interval < 0:
        parser.error("--scan-time must be positive and --refresh-interval must be non-negative")
    if args.path_loss <= 0:
        parser.error("--path-loss must be positive")

    if args.fast:
        args.scan_time = min(args.scan_time, 0.2)
        args.refresh_interval = 0.0
        args.window_size = min(args.window_size, 2)
        args.max_missed = 1

    if args.json_output and not (args.list or args.diagnose):
        parser.error("--json can only be used with --list or --diagnose")
    if args.json_lines and (args.list or args.diagnose):
        parser.error("--json-lines is only for live tracking")
    
    # If no targets provided and not listing, default to choose mode
    if not args.list and not args.diagnose and not (args.target_name or args.target_address):
        args.choose = True

    return args


def normalize_name(device, advertisement_data) -> str:
    if advertisement_data and getattr(advertisement_data, "local_name", None):
        return advertisement_data.local_name
    if getattr(device, "name", None):
        return device.name
    return "Unknown"


def extract_rssi(device, advertisement_data) -> Optional[int]:
    adv_rssi = getattr(advertisement_data, "rssi", None) if advertisement_data else None
    if adv_rssi is not None:
        return adv_rssi
    return getattr(device, "rssi", None)


def extract_tx_power(advertisement_data) -> Optional[int]:
    if not advertisement_data:
        return None
    return getattr(advertisement_data, "tx_power", None)


async def discover_with_advertisements(timeout: float):
    try:
        return await BleakScanner.discover(timeout=timeout, return_adv=True)
    except TypeError:
        devices = await BleakScanner.discover(timeout=timeout)
        return {device.address: (device, None) for device in devices}


def iter_devices(discovery_result) -> Iterable[Tuple[object, object]]:
    if isinstance(discovery_result, dict):
        return discovery_result.values()
    return discovery_result


def estimate_distance_meters(
    rssi: float,
    reference_rssi_at_one_meter: int,
    path_loss_exponent: float,
) -> float:
    # RSSI-based ranging is inherently noisy; this is only a rough estimate.
    ratio = (reference_rssi_at_one_meter - rssi) / (10 * path_loss_exponent)
    return 10 ** ratio


def representative_rssi(window: RssiWindow) -> Optional[float]:
    median = window.median()
    robust_average = window.robust_average()
    if median is None:
        return None
    if robust_average is None:
        return median
    return (median * 0.65) + (robust_average * 0.35)


def quantize_distance_cm(distance_m: float) -> float:
    cm = max(distance_m * 100, 1.0)
    if cm < 100:
        bucket = 5
    elif cm < 300:
        bucket = 10
    else:
        bucket = 25
    return round(cm / bucket) * bucket


def confidence_label(window: RssiWindow) -> str:
    spread = window.spread()
    if len(window) < 4 or spread is None:
        return "low"
    if spread <= 2:
        return "high"
    if spread <= 5:
        return "medium"
    return "low"


def proximity_label(distance_m: float) -> str:
    if distance_m < 0.5:
        return "immediate"
    if distance_m < 1.5:
        return "very-near"
    if distance_m < 4.0:
        return "near"
    if distance_m < 10.0:
        return "far"
    return "very-far"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def print_json(payload: object) -> None:
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")
    sys.stdout.flush()


def print_json_line(payload: object) -> None:
    print(json.dumps(payload), flush=True)


def open_log_writer(path: Path) -> Tuple[object, csv.DictWriter]:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = path.exists()
    handle = path.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(
        handle,
        fieldnames=[
            "timestamp",
            "address",
            "name",
            "rssi",
            "smoothed_rssi",
            "raw_distance_m",
            "estimated_distance_m",
            "estimated_distance_cm",
            "confidence",
            "proximity",
        ],
    )
    if not file_exists:
        writer.writeheader()
    return handle, writer


def format_row(reading: DeviceReading) -> str:
    return (
        f"{reading.timestamp} | {reading.name} | {reading.address} | "
        f"RSSI {reading.rssi:>4} dBm | filt {reading.smoothed_rssi:>6.1f} dBm | "
        f"raw ~{reading.raw_distance_m:>6.2f} m | "
        f"~{reading.estimated_distance_m:>6.2f} m | "
        f"~{reading.estimated_distance_cm:>7.0f} cm | "
        f"{reading.proximity:>10} | "
        f"confidence={reading.confidence}"
    )


async def list_devices(scan_time: float, json_output: bool = False) -> None:
    discovery_result = await discover_with_advertisements(scan_time)
    rows = []

    for device, advertisement_data in iter_devices(discovery_result):
        rssi = extract_rssi(device, advertisement_data)
        rows.append(
            {
                "name": normalize_name(device, advertisement_data),
                "address": getattr(device, "address", "Unknown"),
                "rssi": rssi,
            }
        )

    rows.sort(key=lambda row: (row["name"].lower(), row["address"]))
    if json_output:
        print_json({"devices": rows})
        return

    if not rows:
        print("No nearby BLE devices found.")
        return

    print("Nearby BLE devices:")
    for row in rows:
        rssi_text = "n/a" if row["rssi"] is None else str(row["rssi"])
        print(f"  {row['name']} | {row['address']} | RSSI {rssi_text}")


async def choose_device_interactively(scan_time: float) -> Optional[Tuple[str, str]]:
    while True:
        print("\nScanning for BLE devices (0.5s)...")
        discovery_result = await discover_with_advertisements(0.5) # Force fast scan for UI
        rows = []

        for device, advertisement_data in iter_devices(discovery_result):
            rows.append(
                (
                    normalize_name(device, advertisement_data),
                    getattr(device, "address", "Unknown"),
                    extract_rssi(device, advertisement_data),
                )
            )

        rows.sort(key=lambda row: (row[0].lower(), row[1]))
        if not rows:
            print("No nearby BLE devices found.")
            choice = input("Press Enter to rescan or type q to quit: ").strip().lower()
            if choice == "q":
                return None
            continue

        print("Nearby BLE devices:")
        for index, (name, address, rssi) in enumerate(rows, start=1):
            rssi_text = "n/a" if rssi is None else str(rssi)
            print(f"  [{index}] {name} | {address} | RSSI {rssi_text}")

        prompt = "Choose a device number to track, r to rescan, or q to quit: "
        choice = input(prompt).strip().lower()

        if choice == "q":
            return None
        if choice == "r":
            continue
        if not choice.isdigit():
            print("Invalid choice.")
            continue

        selected_index = int(choice)
        if not 1 <= selected_index <= len(rows):
            print("Choice out of range.")
            continue

        name, address, _ = rows[selected_index - 1]
        return name, address


async def diagnose_devices(scan_time: float, json_output: bool = False) -> None:
    discovery_result = await discover_with_advertisements(scan_time)
    rows = list(iter_devices(discovery_result))
    if not rows:
        if json_output:
            print_json({"devices": []})
            return
        print("No nearby BLE devices found.")
        return

    diagnostics = []
    for device, advertisement_data in rows:
        name = normalize_name(device, advertisement_data)
        address = getattr(device, "address", "Unknown")
        rssi = extract_rssi(device, advertisement_data)
        tx_power = extract_tx_power(advertisement_data)
        service_uuids = getattr(advertisement_data, "service_uuids", []) if advertisement_data else []
        manufacturer_data = getattr(advertisement_data, "manufacturer_data", {}) if advertisement_data else {}
        local_name = getattr(advertisement_data, "local_name", None) if advertisement_data else None
        diagnostics.append(
            {
                "name": name,
                "address": address,
                "rssi": rssi,
                "tx_power": tx_power,
                "advertisement_local_name": local_name,
                "service_uuid_count": len(service_uuids),
                "manufacturer_data_count": len(manufacturer_data),
                "service_uuids": list(service_uuids),
                "manufacturer_ids": [str(company_id) for company_id in manufacturer_data.keys()],
            }
        )

    if json_output:
        diagnostics.sort(key=lambda row: (row["name"].lower(), row["address"]))
        print_json({"devices": diagnostics})
        return

    print("BLE advertisement diagnostics:")
    for row in diagnostics:
        print("")
        print(f"Name: {row['name']}")
        print(f"Address: {row['address']}")
        print(f"RSSI: {'n/a' if row['rssi'] is None else row['rssi']}")
        print(f"TX Power: {'n/a' if row['tx_power'] is None else row['tx_power']}")
        print(f"Advertisement local name: {row['advertisement_local_name'] or 'n/a'}")
        print(f"Service UUID count: {row['service_uuid_count']}")
        print(f"Manufacturer data entries: {row['manufacturer_data_count']}")

        if row["service_uuids"]:
            print(f"Service UUIDs: {', '.join(row['service_uuids'])}")
        if row["manufacturer_ids"]:
            print(f"Manufacturer IDs: {', '.join(row['manufacturer_ids'])}")


async def track_device(args: argparse.Namespace) -> None:
    # Prepare targets
    target_list = []
    if args.target_address:
        for addr in args.target_address.split(","):
            if addr.strip():
                target_list.append(Target(address=addr.strip()))
    if args.target_name:
        for name in args.target_name.split(","):
            if name.strip():
                # Check if we already have a target with this name (from address split)
                target_list.append(Target(name=name.strip()))
    
    if not target_list:
        print("No targets specified for tracking.")
        return

    manager = TargetManager(target_list)

    if args.json_lines:
        print_json_line(
            {
                "type": "status",
                "timestamp": iso_now(),
                "message": SAFETY_NOTICE,
            }
        )
    else:
        print(SAFETY_NOTICE)
        print("")
        print(f"Tracking targets: {', '.join(str(t) for t in target_list)}")
        if msvcrt:
            print("Press 'n' to cycle between targets, Ctrl+C to stop.")
        else:
            print("Press Ctrl+C to stop.")
        print("")

    window = RssiWindow(args.window_size)
    distance_filter = DistanceFilter()
    missed_scans = 0
    log_handle = None
    log_writer = None
    scanner = None
    event_queue: asyncio.Queue[Tuple[object, object]] = asyncio.Queue()

    if args.log:
        log_handle, log_writer = open_log_writer(args.log)

    def detection_callback(device, advertisement_data) -> None:
        address = getattr(device, "address", "") or ""
        name = normalize_name(device, advertisement_data)
        if manager.matches(address, name):
            event_queue.put_nowait((device, advertisement_data))

    async def keyboard_listener():
        # Handle both interactive keyboard (msvcrt) and piped stdin (for backend)
        loop = asyncio.get_event_loop()
        cmd_queue = asyncio.Queue()

        def on_stdin():
            line = sys.stdin.readline().strip().lower()
            if line:
                cmd_queue.put_nowait(line[0])

        if not msvcrt:
            # If no msvcrt (e.g. running under Node.js), use stdin reader
            try:
                loop.add_reader(sys.stdin, on_stdin)
            except (NotImplementedError, AttributeError):
                # Fallback for systems/event loops where add_reader isn't available
                pass

        while True:
            char = None
            if msvcrt and msvcrt.kbhit():
                char = msvcrt.getch().decode().lower()
            
            # Also check if a command came via the stdin reader
            if not char and not cmd_queue.empty():
                char = await cmd_queue.get()

            if char:
                if char == 'n':
                    new_target = manager.cycle()
                    if not args.json_lines:
                        print(f"\n>>> Switched to target: {new_target}")
                    window._values.clear()
                    distance_filter._distance_m = None
                elif char == 'b':
                    if not msvcrt: loop.remove_reader(sys.stdin)
                    return "back"
                elif char == 'q':
                    if not msvcrt: loop.remove_reader(sys.stdin)
                    return "quit"
            
            await asyncio.sleep(0.1)

    kb_task = asyncio.create_task(keyboard_listener())

    try:
        scanner = BleakScanner(detection_callback=detection_callback)
        await scanner.start()
        
        while True:
            # Check if keyboard listener finished with a signal
            if kb_task.done():
                return kb_task.result()

            try:
                device, advertisement_data = await asyncio.wait_for(
                    event_queue.get(),
                    timeout=0.1, # Short timeout to keep loop responsive to kb_task
                )
            except asyncio.TimeoutError:
                # Still check missed scans, but on a separate timer or logic
                continue

            # Only process if it matches the CURRENTLY ACTIVE target
            address = getattr(device, "address", "Unknown")
            name = normalize_name(device, advertisement_data)
            if not manager.is_active(address, name):
                continue

            missed_scans = 0
            rssi = extract_rssi(device, advertisement_data)
            if rssi is None:
                continue
            
            if window.is_outlier(rssi):
                continue

            window.add(rssi)
            smoothed_rssi = representative_rssi(window)
            if smoothed_rssi is None:
                continue

            raw_distance_m = estimate_distance_meters(
                rssi=smoothed_rssi,
                reference_rssi_at_one_meter=args.reference_rssi,
                path_loss_exponent=args.path_loss,
            )
            confidence = confidence_label(window)
            distance_m = distance_filter.update(raw_distance_m, confidence)
            distance_cm = quantize_distance_cm(distance_m)
            reading = DeviceReading(
                timestamp=iso_now(),
                address=address,
                name=name,
                rssi=rssi,
                smoothed_rssi=smoothed_rssi,
                raw_distance_m=raw_distance_m,
                estimated_distance_m=distance_m,
                estimated_distance_cm=distance_cm,
                confidence=confidence,
                proximity=proximity_label(distance_m),
            )
            if args.json_lines:
                print_json_line(
                    {
                        "type": "reading",
                        "timestamp": reading.timestamp,
                        "reading": asdict(reading),
                    }
                )
            else:
                print(format_row(reading))
            if log_writer:
                log_writer.writerow(reading.__dict__)
                log_handle.flush()

            if args.refresh_interval > 0:
                await asyncio.sleep(args.refresh_interval)
    finally:
        if scanner is not None:
            try:
                await scanner.stop()
            except Exception:
                pass
        if log_handle:
            log_handle.close()


async def main() -> None:
    args = parse_args()
    
    if args.list:
        await list_devices(args.scan_time, args.json_output)
        return
    if args.diagnose:
        await diagnose_devices(args.scan_time, args.json_output)
        return

    # Main application loop
    while True:
        # 1. Selection Phase
        if args.target_name or args.target_address:
            # If targets provided via CLI/.env, use them directly the first time
            pass
        else:
            selection = await choose_device_interactively(args.scan_time)
            if selection is None:
                break # User quit from selection
            
            selected_name, selected_address = selection
            args.target_name = selected_name
            args.target_address = selected_address
            print(f"\nTarget set to: {selected_name} | {selected_address}")

        # 2. Tracking Phase
        print(f"\nStarting high-speed tracker for: {args.target_name or args.target_address}")
        result = await track_device(args)
        
        if result == "quit":
            print("\nQuitting...")
            break
        elif result == "back":
            print("\nReturning to device selection...")
            # Clear targets to force re-selection
            args.target_name = None
            args.target_address = None
            continue
        else:
            # KeyboardInterrupt or other stop
            break


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        if "--json-lines" in sys.argv:
            print_json_line(
                {
                    "type": "stopped",
                    "timestamp": iso_now(),
                    "message": "Stopped.",
                }
            )
        else:
            print("\nStopped.")
    except Exception as exc:
        if "--json" in sys.argv:
            print_json({"error": str(exc)})
        elif "--json-lines" in sys.argv:
            print_json_line(
                {
                    "type": "error",
                    "timestamp": iso_now(),
                    "message": str(exc),
                }
            )
        else:
            print(f"Error: {exc}")
        sys.exit(1)
