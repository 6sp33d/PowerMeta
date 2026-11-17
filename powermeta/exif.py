"""Metadata extraction with ExifTool (https://exiftool.org/).

ExifTool before 12.24 can be made to run code by a crafted file (CVE-2021-22204), and PowerMeta runs it
on files downloaded from the target, so older copies are refused.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import zipfile

from .web import FetchError, Fetcher, download

MINIMUM_VERSION = (12, 24)


def _on_path(name):
    """Look for a program on the PATH. Unlike shutil.which on Windows this never looks in the current
    directory, which may be a folder of files that were downloaded from the target."""
    # On Windows only a real program counts: a .cmd or .bat file would hand its arguments to cmd.exe
    extensions = [".exe"] if os.name == "nt" else [""]
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        folder = folder.strip('"')
        if not folder or not os.path.isabs(folder):
            continue
        for extension in extensions:
            candidate = os.path.join(folder, name + extension)
            if os.path.isfile(candidate) and (os.name == "nt" or os.access(candidate, os.X_OK)):
                return candidate
    return None


def install_folder():
    """The folder that holds the PowerMeta scripts: ExifTool is installed, and looked for, there."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_exiftool():
    """ExifTool next to the PowerMeta scripts, or on the PATH. None if it is not installed."""
    name = "exiftool.exe" if os.name == "nt" else "exiftool"
    candidate = os.path.join(install_folder(), name)
    if os.path.isfile(candidate):
        return candidate
    return _on_path("exiftool")


def exiftool_version(path):
    """The version ExifTool reports, as a tuple such as (13, 64), or None if it does not report one."""
    try:
        completed = subprocess.run([path, "-ver"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.fullmatch(r"(\d+)\.(\d+)", completed.stdout.decode("ascii", errors="replace").strip())
    return (int(match.group(1)), int(match.group(2))) if match else None


def usable_exiftool(log=print):
    """Find ExifTool and check that it is safe to use. Returns its path, or None after explaining why not."""
    path = find_exiftool()
    if not path:
        log(
            "[*] ExifTool was not found next to PowerMeta or on the PATH. On Windows run "
            "'python -m powermeta --install-exiftool'; elsewhere install it with your package manager."
        )
        return None
    version = exiftool_version(path)
    if version is None:
        log(
            "[!] %s did not report a version, so it cannot be used. On Windows the 'exiftool_files' folder "
            "must sit next to exiftool.exe." % path
        )
        return None
    if version < MINIMUM_VERSION:
        log(
            "[!] %s is ExifTool %d.%02d. Versions before 12.24 can be made to run code by a crafted file "
            "(CVE-2021-22204), so it will not be used on downloaded files. Install the current release."
            % (path, version[0], version[1])
        )
        return None
    return path


def _run(path, options, folder):
    # "-ext *" makes exiftool read every file, also those without a known extension. The arguments go
    # to the program as a list, so no shell ever sees a file name.
    completed = subprocess.run(
        [path] + list(options) + ["-ext", "*", os.path.abspath(folder)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=3600,
    )
    return completed.stdout


def extract_authors(path, folder):
    """The 'Author' and 'Creator' values of the files in a folder.

    Returns (number of files read, sorted list of distinct values).
    """
    output = _run(path, ["-j", "-q", "-q", "-Author", "-Creator"], folder).decode("utf-8", errors="replace").strip()
    records = json.loads(output) if output else []
    values = set()
    for record in records:
        for tag in ("Author", "Creator"):
            value = record.get(tag)
            for item in value if isinstance(value, list) else [value]:
                text = "" if item is None else str(item).strip()
                if text:
                    values.add(text)
    return len(records), sorted(values, key=lambda text: (text.lower(), text))


def extract_all_to_csv(path, folder, csv_path):
    """Write every metadata field of the files in a folder to a CSV file. Returns the number of files."""
    output = _run(path, ["-csv", "-q", "-q"], folder).decode("utf-8", errors="replace")
    rows = list(csv.reader(io.StringIO(output)))
    if len(rows) < 2:
        return 0
    # The byte-order mark makes spreadsheet programs read the file as UTF-8
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as target:
        target.write(output)
    return len(rows) - 1


def install_exiftool(destination=None, log=print):
    """Windows: download the current official ExifTool package, check it against the SHA-256 checksum
    published on exiftool.org, and unpack exiftool.exe and its exiftool_files folder next to PowerMeta.
    Returns True if it was installed."""
    if os.name != "nt":
        log(
            "[*] --install-exiftool is for Windows. On Linux or macOS install ExifTool with your package "
            "manager, for example 'apt install libimage-exiftool-perl' or 'brew install exiftool'."
        )
        return False

    destination = os.path.abspath(destination or install_folder())
    # A plain agent name: the download mirror answers browser-like agents with a web page instead of the file
    agent = "PowerMeta"
    fetcher = Fetcher(user_agent=agent)
    try:
        version = fetcher.get("https://exiftool.org/ver.txt").text.strip()
        if not re.fullmatch(r"\d+\.\d+", version):
            log("[!] Could not read the current ExifTool version from https://exiftool.org/ver.txt. Nothing was installed.")
            return False

        bits = "64" if platform.machine().endswith("64") else "32"
        package = "exiftool-%s_%s" % (version, bits)
        zip_name = package + ".zip"

        checksums = fetcher.get("https://exiftool.org/checksums.txt").text
        match = re.search(r"SHA2-256\(" + re.escape(zip_name) + r"\)\s*=\s*([0-9a-fA-F]{64})", checksums)
        if not match:
            log("[!] No SHA-256 checksum for %s is listed on https://exiftool.org/checksums.txt. Nothing was installed." % zip_name)
            return False
        expected = match.group(1).lower()

        with tempfile.TemporaryDirectory(prefix="powermeta-exiftool-") as work:
            zip_path = os.path.join(work, zip_name)
            log("[*] Downloading ExifTool %s (%s)" % (version, zip_name))
            download(
                "https://sourceforge.net/projects/exiftool/files/%s/download" % zip_name,
                zip_path,
                user_agent=agent,
                timeout=600,
            )

            digest = hashlib.sha256()
            with open(zip_path, "rb") as source:
                for block in iter(lambda: source.read(1 << 20), b""):
                    digest.update(block)
            if digest.hexdigest() != expected:
                log(
                    "[!] The download does not match the checksum published on exiftool.org "
                    "(expected %s, got %s). Nothing was installed." % (expected, digest.hexdigest())
                )
                return False
            log("[*] Checksum verified.")

            unpacked = os.path.join(work, "unpacked")
            with zipfile.ZipFile(zip_path) as archive:
                archive.extractall(unpacked)

            # The package holds "exiftool(-k).exe", which has to be named exiftool.exe for command-line use,
            # and the folder "exiftool_files"
            launcher = os.path.join(unpacked, package, "exiftool(-k).exe")
            files = os.path.join(unpacked, package, "exiftool_files")
            if not os.path.isfile(launcher) or not os.path.isdir(files):
                log("[!] The ExifTool package does not have the expected layout. Nothing was installed.")
                return False

            os.makedirs(destination, exist_ok=True)
            target_files = os.path.join(destination, "exiftool_files")
            if os.path.exists(target_files):
                log("[*] Replacing the existing %s" % target_files)
                shutil.rmtree(target_files)
            shutil.copytree(files, target_files)
            shutil.copyfile(launcher, os.path.join(destination, "exiftool.exe"))

        log("[*] ExifTool %s was installed to %s" % (version, destination))
        return True
    except (FetchError, OSError, zipfile.BadZipFile) as error:
        log("[!] ExifTool could not be installed: %s" % error)
        return False
