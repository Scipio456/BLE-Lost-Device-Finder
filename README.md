# Earpods BLE Tracker

`earpods_tracker.py` is a local Python CLI tool that scans for nearby Bluetooth Low Energy earbuds, tracks a selected device live, and estimates distance from RSSI.

Important limit: this does not measure exact physical distance. It gives a live estimate only.

## Ethical Safety

- Use it only for earbuds or BLE devices that you own or are explicitly allowed to track.
- The tool keeps data on your machine.
- It does not upload anything.
- It does not write logs unless you pass `--log`.
- It is not intended for tracking people.

## Requirements

- Windows with Bluetooth enabled
- Python 3.10+ recommended
- A Bluetooth adapter that exposes BLE advertisement data

## What You Need To Install

Only two external packages are required:

- `bleak`
- `python-dotenv`

Create a virtual environment and install them there:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Configuration (.env)

For easier tracking and to keep your device information safe from Git, copy `.env.example` to `.env` and fill in your targets:

```powershell
cp .env.example .env
```

Edit `.env`:
```env
TARGET_ADDRESSES=AA:BB:CC:DD:EE:FF,11:22:33:44:55:66
TARGET_NAMES=My Earbuds,Other Buds
```

The tracker will automatically load these targets on startup.

## Files

- `earpods_tracker.py`: main tracker script
- `tracker-cli.js`: Node.js CLI wrapper that runs the Python tracker
- `server.js`: Node.js backend API that runs the Python tracker
- `package.json`: Node.js backend scripts
- `requirements.txt`: Python dependency list
- `.gitignore`: keeps local venvs, logs, caches, and environment files out of Git
- `SECURITY.md`: privacy and safe-use notes

## GitHub Upload Notes

This repository is safe to upload through Git when you commit only the tracked source files.

Do not upload:

- `.venv/`
- `.tmp/`
- `.pip-tmp/`
- `logs/`
- `__pycache__/`
- `.env`
- CSV logs from tracker runs

Bluetooth scan output can include personal device names and addresses. Keep screenshots, CSV logs, and copied terminal output private unless you intentionally anonymize them.

## Quick Start

### Python CLI

1. Install the Python dependency.
2. Run the tracker.
3. Choose a device from the scanned list.
4. Start live tracking.

Commands:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe earpods_tracker.py
```

If `python` is not recognized on your machine, use `py` instead:

```powershell
py -m pip install -r requirements.txt
py earpods_tracker.py
```

### Node.js Backend CLI Wrapper

Use this when you want Node.js to run the existing Python CLI program.

Install the Python dependency in the project venv first:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Run the interactive tracker through Node.js:

```powershell
node tracker-cli.js
```

List nearby BLE devices through Node.js:

```powershell
node tracker-cli.js --list
```

Track by name through Node.js:

```powershell
node tracker-cli.js --target-name "My Earbuds"
```

Track by address through Node.js:

```powershell
node tracker-cli.js --target-address "<BLUETOOTH_ADDRESS>"
```

The same commands are also available as npm scripts:

```powershell
npm.cmd run tracker
npm.cmd run list
npm.cmd run diagnose
```

To pass extra options through npm, put them after `--`:

```powershell
npm.cmd run tracker -- --target-name "My Earbuds" --fast
```

If your machine uses the Windows `py` launcher instead of `python`, set:

```powershell
$env:PYTHON_BIN="py"
node tracker-cli.js --list
```

When `.venv` exists, `tracker-cli.js` automatically uses `.venv\Scripts\python.exe`.

### Python + Node.js Backend

The Node.js backend keeps the Bluetooth work in Python and exposes it as local HTTP APIs.

Requirements:

- Python dependency installed in `.venv`
- Node.js 18 or newer

The backend automatically uses `.venv\Scripts\python.exe` when the venv exists.

Start the backend:

```powershell
npm start
```

The server runs at:

```text
http://127.0.0.1:3000
```

If your machine uses the Windows `py` launcher instead of `python`, start it with:

```powershell
$env:PYTHON_BIN="py"
npm start
```

You can also change the port:

```powershell
$env:PORT="4000"
npm start
```

Backend endpoints:

```text
GET /health
GET /api/devices?scanTime=2
GET /api/diagnostics?scanTime=2
GET /api/track/stream?targetName=My%20Earbuds
GET /api/track/stream?targetAddress=<BLUETOOTH_ADDRESS>&fast=true
```

The live tracking endpoint uses Server-Sent Events. Each connected client starts one Python tracker process, and closing the HTTP connection stops that process.

Example scan:

```powershell
Invoke-RestMethod "http://127.0.0.1:3000/api/devices?scanTime=2"
```

Example browser/EventSource client:

```javascript
const stream = new EventSource(
  "http://127.0.0.1:3000/api/track/stream?targetName=My%20Earbuds"
);

stream.addEventListener("reading", (event) => {
  console.log(JSON.parse(event.data));
});
```

## Basic Usage

### Default Interactive Mode

If you run the script without `--target-name` or `--target-address`, it scans nearby BLE devices and lets you choose one from a numbered list.

```powershell
python earpods_tracker.py
```

You can also force that mode explicitly:

```powershell
python earpods_tracker.py --choose
```

### 1. List Nearby Devices

Use this first to find the exact name or address your earbuds advertise.

```powershell
python earpods_tracker.py --list
```

Example output:

```text
Nearby BLE devices:
  My Earbuds | <BLUETOOTH_ADDRESS> | RSSI -54
```

### 2. Diagnose BLE Advertisement Details

Use this if a device appears but behaves strangely, or if RSSI is `n/a`.

```powershell
python earpods_tracker.py --diagnose
```

This prints:

- RSSI
- TX power if available
- advertisement local name
- service UUID count
- manufacturer data count and IDs

### 3. Track by Device Name

The script matches the first nearby device whose name contains the text you provide.

```powershell
python earpods_tracker.py --target-name "My Earbuds"
```

### 4. Track by Exact Address

Use this when device names are duplicated or unstable.

```powershell
python earpods_tracker.py --target-address "<BLUETOOTH_ADDRESS>"
```

## Common Commands

Track normally:

```powershell
python earpods_tracker.py --target-name "My Earbuds"
```

Track as fast as possible:

```powershell
python earpods_tracker.py --target-name "My Earbuds" --fast
```

Track fast with minimal smoothing:

```powershell
python earpods_tracker.py --target-name "My Earbuds" --fast --window-size 1
```

Track more steadily indoors:

```powershell
python earpods_tracker.py --target-name "My Earbuds" --window-size 8 --path-loss 3.0
```

Track and write a local CSV log:

```powershell
python earpods_tracker.py --target-name "My Earbuds" --log logs\earbuds.csv
```

Track by address with a custom calibration:

```powershell
python earpods_tracker.py --target-address "<BLUETOOTH_ADDRESS>" --reference-rssi -54 --path-loss 3.0
```

## Navigation & Controls

While tracking, you can use the following keys (on Windows):

- **'n'**: Cycle to the next target (if multiple targets were specified in `.env`).
- **'b'**: **Back** to the main device selection list. Stops tracking the current device.
- **'q'**: **Quit** the application.

## Multi-Device Tracking

## Command-Line Options

```text
--list
    List nearby BLE devices and exit.

--diagnose
    Show raw BLE advertisement details and exit.

--choose
    Scan nearby devices and choose one interactively before tracking.

--target-name TEXT
    Track the first nearby device whose name contains TEXT.

--target-address ADDRESS
    Track one exact BLE address.

--scan-time SECONDS
    Scan window used for missed-device timeout logic.
    Default: 1.0

--refresh-interval SECONDS
    Optional delay after printing each update.
    Default: 0.2

--window-size NUMBER
    RSSI smoothing window.
    Larger values are more stable but slower.
    Default: 5

--reference-rssi NUMBER
    Calibrated RSSI at exactly 1 meter.
    Default: -59

--path-loss NUMBER
    Indoor attenuation factor.
    Typical indoor range: 2.0 to 4.0
    Default: 2.2

--log PATH
    Write readings to a local CSV file.

--max-missed NUMBER
    Warn after this many missed scan windows.
    Default: 3

--fast
    Use aggressive low-latency tracking.
    Faster, but less stable.
```

## Understanding The Output

A live line looks like this:

```text
2026-03-12T12:00:00+00:00 | My Earbuds | <BLUETOOTH_ADDRESS> | RSSI -54 dBm | filt  -55.1 dBm | raw ~  0.66 m | ~  0.72 m | ~     70 cm |  very-near | confidence=medium
```

Meaning:

- `RSSI`: the latest signal strength reading
- `filt`: the filtered RSSI used for estimation
- `raw`: the immediate distance estimate before stabilization
- `~ Xm`: the stabilized distance estimate in meters
- `~ Y cm`: the stabilized distance estimate in centimeters
- `proximity`: a coarse label such as `immediate`, `very-near`, `near`, `far`
- `confidence`: how stable recent RSSI readings are

Trust the stabilized distance more than the raw distance.

## How To Search For Lost Earbuds

Use the tool like a hotter/colder detector:

1. Start tracking.
2. Move slowly through the room.
3. Watch whether RSSI gets stronger and the stabilized distance trends down.
4. Turn your body and laptop slowly and compare readings.
5. Recheck from a few positions instead of trusting one spike.

For actual searching, signal trend is more useful than the exact meter or centimeter number.

## Accuracy Tuning

### For Faster Reaction

Use:

```powershell
python earpods_tracker.py --target-name "My Earbuds" --fast
```

Tradeoff: readings react quickly but jump more.

### For More Stable Room-Scale Searching

Use:

```powershell
python earpods_tracker.py --target-name "My Earbuds" --window-size 8 --path-loss 3.0
```

Tradeoff: readings are slower but usually more believable indoors.

### Calibrate `--reference-rssi`

If you want a better estimate for your specific laptop and earbuds:

1. Place the earbuds exactly 1 meter from your laptop.
2. Run tracking for a short time.
3. Note the filtered RSSI.
4. Re-run using that value as `--reference-rssi`.

Example:

```powershell
python earpods_tracker.py --target-name "My Earbuds" --reference-rssi -54
```

### Tune `--path-loss`

- `2.0` to `2.4`: open spaces, less obstruction
- `2.8` to `3.5`: typical indoor rooms with walls, furniture, and body blocking
- higher values usually reduce unrealistic "too close" estimates indoors

## Logging

To save readings locally:

```powershell
python earpods_tracker.py --target-name "My Earbuds" --log logs\earbuds.csv
```

The CSV contains:

- `timestamp`
- `address`
- `name`
- `rssi`
- `smoothed_rssi`
- `raw_distance_m`
- `estimated_distance_m`
- `estimated_distance_cm`
- `confidence`
- `proximity`

## Troubleshooting

### Device Shows Up But RSSI Is `n/a`

Run:

```powershell
python earpods_tracker.py --diagnose
```

Likely causes:

- the earbuds are not exposing BLE RSSI at that moment
- they are inside the charging case
- they are sleeping or powered off
- your Bluetooth adapter or Windows driver is not exposing the necessary BLE advertisement fields

### Readings Jump Around Too Much

Try:

```powershell
python earpods_tracker.py --target-name "My Earbuds" --window-size 8 --path-loss 3.0
```

Also:

- move slowly
- keep the laptop orientation consistent while comparing readings
- avoid judging from a single packet
- keep the earbuds out of the case

### The Tracker Says The Earbuds Are Closer Than They Really Are

This is common indoors. Try:

- increasing `--path-loss` to `3.0` or `3.2`
- calibrating `--reference-rssi` at 1 meter
- using a larger `--window-size`

### The Device Disappears From Tracking

Possible reasons:

- earbuds entered sleep mode
- earbuds went back into the case
- battery is low or dead
- another device connected and changed advertising behavior

## Technical Limits

- Standard Bluetooth RSSI cannot give exact distance.
- A normal laptop or phone cannot reliably determine direction from a single Bluetooth radio.
- Walls, furniture, your body, Wi-Fi, and reflections can distort readings heavily.
- Earbuds may advertise irregularly, especially when connected for audio.
- The centimeter display is still an estimate, not a measurement.

## Recommended Defaults

For most users:

- quick search: `--fast`
- steadier room search: `--window-size 8 --path-loss 3.0`
- if calibrating at 1 meter: also set `--reference-rssi` to the observed filtered RSSI

## Stop Tracking

Press `Ctrl+C` to stop the program.
