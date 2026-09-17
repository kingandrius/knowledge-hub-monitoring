# Task 11 - Python Monitoring Script

## 1. Monitoring Application

The monitoring application is written in Python and is used to measure
system resources on the computer where the script is running.

The application supports three metrics:

- CPU usage
- Memory usage
- Disk usage

The measurements are collected using the Python `psutil` library.

## 2. How Data Is Collected

The application uses `psutil` to collect system resource usage.

### CPU

CPU usage is measured using:

```python
psutil.cpu_percent(interval=1)
```

The application measures CPU usage over a one-second interval.

### Memory

Memory usage is measured using:

```python
psutil.virtual_memory().percent
```

This records the percentage of memory currently in use.

### Disk

Disk usage is measured using:

```python
psutil.disk_usage("/").percent
```

This records the percentage of the selected disk filesystem that is in use.

Each measurement is given a timestamp using the format:

```text
YYYY-MM-DD HH:MM:SS
```

## 3. Database Storage

All measurements recorded by the application are stored in a local SQLite
database called:

```text
monitoring.db
```

The database contains a table called:

```text
measurements
```

The table contains the following fields:

| Field | Description |
|---|---|
| `id` | Unique identifier for the measurement |
| `timestamp` | Date and time when the measurement was taken |
| `metric` | Name of the measured metric |
| `value` | Measured value |

An example record is:

```text
1 | 2026-09-17 10:56:10 | cpu | 13.8
```

The application adds new measurements to the database rather than
overwriting existing measurements.

This allows measurements from previous executions to remain available
when a report is requested.

## 4. Running the Application

The application is a command-line application and does not use interactive
input. The required mode and optional settings are provided as command-line
arguments.

The application has two modes:

- `measure` - collect and store new measurements
- `report` - retrieve and display stored measurements

### Measure Mode

To measure all supported metrics:

```powershell
python monitoring.py measure
```

To measure specific metrics:

```powershell
python monitoring.py measure --metrics cpu
```

```powershell
python monitoring.py measure --metrics cpu,memory,disk
```

The supported metrics are:

```text
cpu
memory
disk
```

New measurements are added to `monitoring.db`.

### Report Mode

To display all stored measurements:

```powershell
python monitoring.py report
```

To display only specific metrics:

```powershell
python monitoring.py report --metrics cpu
```

Multiple metrics can be selected:

```powershell
python monitoring.py report --metrics cpu,memory
```

### Time Filtering

Reports can be filtered using a start time:

```powershell
python monitoring.py report --start "2026-09-17 10:56:20"
```

A start and end time can also be specified:

```powershell
python monitoring.py report --start "2026-09-17 10:00:00" --end "2026-09-17 11:00:00"
```

The required time format is:

```text
YYYY-MM-DD HH:MM:SS
```

### Calculating Averages

The `--average` option calculates the average value for each metric
included in the report.

For example:

```powershell
python monitoring.py report --metrics cpu,memory,disk --average
```

The application first filters the stored measurements and then calculates
the average for each selected metric.

## 5. Example Output

A measurement command produces a confirmation message:

```text
Measurements saved successfully.
```

A report is displayed as a table:

```text
MONITORING REPORT
-----------------------------------------------------------------
Timestamp            Metric               Value
-----------------------------------------------------------------
2026-09-17 10:56:10  cpu                   13.8%
2026-09-17 10:56:10  memory                73.2%
2026-09-17 10:56:10  disk                  43.3%
-----------------------------------------------------------------
```

When averages are requested, an additional section is displayed:

```text
AVERAGES
----------------------------------------
cpu                  17.10%
memory               73.20%
disk                 43.30%
----------------------------------------
```

## 6. Data Persistence

Measurements are stored permanently in the local SQLite database.

The application uses SQLite's `INSERT` operation to add new records.
Existing measurements are not overwritten during normal operation.

This means measurements remain available after the monitoring application
has been closed and started again.