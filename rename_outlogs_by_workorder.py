import argparse
import csv
import re
import sys
from pathlib import Path


OLD_NAME_PATTERN = re.compile(
    r"^(?P<date>\d{8})_(?P<time>\d{6})_(?P<mac1>[0-9A-F]+)_(?P<mac2>[0-9A-F]+)_(?P<result>PASS|FAIL|STOP)\.txt$",
    re.IGNORECASE,
)

NEW_NAME_PATTERN = re.compile(
    r"^(?P<wo>[A-Za-z0-9]+-\d{8,})_(?P<date>\d{8})_(?P<time>\d{6})_(?P<mac1>[0-9A-F]+)_(?P<mac2>[0-9A-F]+)_(?P<result>PASS|FAIL|STOP)\.txt$",
    re.IGNORECASE,
)


def load_mac_to_workorder(csv_path: Path):
    mac_to_workorder = {}
    duplicate_conflicts = {}

    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        required_columns = {"Working order", "MAC"}
        missing_columns = required_columns - set(reader.fieldnames or [])
        if missing_columns:
            missing_text = ", ".join(sorted(missing_columns))
            raise ValueError(f"CSV is missing required columns: {missing_text}")

        for row in reader:
            mac = (row.get("MAC") or "").strip().upper()
            workorder = (row.get("Working order") or "").strip()
            if not mac or not workorder:
                continue

            existing_workorder = mac_to_workorder.get(mac)
            if existing_workorder and existing_workorder != workorder:
                duplicate_conflicts.setdefault(mac, set()).update({existing_workorder, workorder})
                continue

            mac_to_workorder[mac] = workorder

    return mac_to_workorder, duplicate_conflicts


def classify_target_name(filename: str, mac_to_workorder):
    if NEW_NAME_PATTERN.match(filename):
        return None, "already_renamed"

    match = OLD_NAME_PATTERN.match(filename)
    if not match:
        return None, "invalid_format"

    date_text = match.group("date")
    time_text = match.group("time")
    mac1 = match.group("mac1").upper()
    mac2 = match.group("mac2").upper()
    result = match.group("result").upper()

    workorder = mac_to_workorder.get(mac1)
    if not workorder:
        return None, "mac1_not_found"

    new_name = f"{workorder}_{date_text}_{time_text}_{mac1}_{mac2}_{result}.txt"
    return new_name, "ready"


def iter_log_files(log_dir: Path, recursive: bool):
    if recursive:
        return sorted(path for path in log_dir.rglob("*.txt") if path.is_file())
    return sorted(path for path in log_dir.glob("*.txt") if path.is_file())


def rename_files(log_dir: Path, csv_path: Path, dry_run: bool, recursive: bool):
    mac_to_workorder, duplicate_conflicts = load_mac_to_workorder(csv_path)

    if duplicate_conflicts:
        print("Warning: conflicting MAC mappings detected in CSV. The first seen workorder is kept:")
        for mac, workorders in sorted(duplicate_conflicts.items()):
            print(f"  {mac}: {', '.join(sorted(workorders))}")
        print()

    renamed_count = 0
    already_renamed_count = 0
    invalid_format_count = 0
    missing_mac_count = 0
    target_exists_count = 0

    for file_path in iter_log_files(log_dir, recursive):
        if file_path.name.lower() == "summary.txt":
            continue

        new_name, status = classify_target_name(file_path.name, mac_to_workorder)

        if status == "already_renamed":
            already_renamed_count += 1
            continue

        if status == "invalid_format":
            invalid_format_count += 1
            print(f"Skip invalid format: {file_path.name}")
            continue

        if status == "mac1_not_found":
            missing_mac_count += 1
            print(f"Skip MAC1 not found in CSV: {file_path.name}")
            continue

        target_path = file_path.with_name(new_name)
        if target_path.exists():
            target_exists_count += 1
            print(f"Skip target already exists: {target_path.name}")
            continue

        print(f"Rename: {file_path.name} -> {target_path.name}")
        if not dry_run:
            file_path.rename(target_path)

        renamed_count += 1

    print()
    print("Rename completed")
    print(f"  Log directory:        {log_dir}")
    print(f"  CSV path:             {csv_path}")
    print(f"  Renamed files:        {renamed_count}")
    print(f"  Already renamed:      {already_renamed_count}")
    print(f"  Invalid name format:  {invalid_format_count}")
    print(f"  Missing MAC mapping:  {missing_mac_count}")
    print(f"  Target exists:        {target_exists_count}")
    print(f"  Dry run:              {dry_run}")

    return 0


def parse_args():
    parser = argparse.ArgumentParser(
        description="Batch rename OUTLOGS files by prepending the working order resolved from MAC1 in CSV."
    )
    parser.add_argument(
        "--log-dir",
        default="rawlogs/OUTLOGS",
        help="Directory containing the split log files. Default: rawlogs/OUTLOGS",
    )
    parser.add_argument(
        "--csv",
        default="PIXI_WO_MAC_TABLE.csv",
        help="CSV file containing Working order and MAC columns. Default: PIXI_WO_MAC_TABLE.csv",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively scan subdirectories under the log directory.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the rename actions without modifying any files.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    log_dir = Path(args.log_dir).resolve()
    csv_path = Path(args.csv).resolve()

    if not log_dir.is_dir():
        print(f"Error: log directory not found: {log_dir}", file=sys.stderr)
        return 1

    if not csv_path.is_file():
        print(f"Error: CSV file not found: {csv_path}", file=sys.stderr)
        return 1

    return rename_files(
        log_dir=log_dir,
        csv_path=csv_path,
        dry_run=args.dry_run,
        recursive=args.recursive,
    )


if __name__ == "__main__":
    raise SystemExit(main())