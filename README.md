# Kickzuru Monitor

Real-time terminal dashboard for monitoring Kickzuru CPU, memory, I/O, threads, child processes, and more.

## Setup

```bash
pip install -r requirements.txt
```

## Quick start (recommended)

The monitor finds Kickzuru by process name automatically:

1. Start Kickzuru
2. Run:

```bash
python monitor.py
```

That's it. By default it looks for a process named `Kickzuru` (matches `Kickzuru.exe` on Windows).

Press **Ctrl+C** to quit.

## If Kickzuru isn't found

Make sure Kickzuru is running, then list matching processes:

```bash
python monitor.py --list
```

Example output:

```
Processes matching 'Kickzuru'
┏━━━━━━┳━━━━━━━━━━━━━━┓
┃  PID ┃ Name         ┃
┡━━━━━━╇━━━━━━━━━━━━━━┩
│ 4321 │ Kickzuru.exe │
└──────┴──────────────┘
```

If multiple instances are running, pick one by PID:

```bash
python monitor.py --pid 4321
```

## Options

| Flag | Description |
|------|-------------|
| `--name Kickzuru` | Find process by name (default) |
| `--pid 4321` | Monitor a specific PID |
| `--list` | List matching processes and exit |
| `--refresh 4` | Refresh rate in Hz (default: 2) |

## Metrics

| Panel | Metrics |
|-------|---------|
| **Process** | CPU %, RSS/private/virtual memory, memory %, threads, handles, status, uptime, disk I/O, connections, context switches, executable path |
| **Process Tree** | Combined usage for Kickzuru + all child processes (WebView2, etc.) |
| **System** | Overall CPU %, RAM, disk usage |
| **Details** | Child processes with CPU, memory, threads, and status |
