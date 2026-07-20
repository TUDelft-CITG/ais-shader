import os
import subprocess
import zipfile
from pathlib import Path
from typing import Iterator, Optional

from dotenv import load_dotenv


def is_encrypted_zip(input_file: Path) -> bool:
    """True if input_file is a zip archive whose first entry is encrypted.

    Some sources (e.g. RWS) deliver AES-encrypted zip archives with a
    ``.7z`` extension even though they aren't real 7z files.
    """
    if not zipfile.is_zipfile(input_file):
        return False
    with zipfile.ZipFile(input_file) as z:
        infos = z.infolist()
        return bool(infos) and bool(infos[0].flag_bits & 0x1)


def find_zip_password(input_file: Path) -> str:
    """Resolve the password for an encrypted zip archive.

    Looks for a ``.env`` file next to input_file (without overriding
    already-exported variables), then returns the value of the first
    environment variable whose name ends in "PASSWORD" (case-insensitive) --
    the RWS dataset uses e.g. AHOD_PASSWORD, other datasets may use a
    different prefix.
    """
    load_dotenv(input_file.parent / ".env", override=False)
    for key, value in os.environ.items():
        if key.upper().endswith("PASSWORD"):
            return value
    raise ValueError(
        f"'{input_file}' is an encrypted zip archive, but no environment "
        f"variable ending in PASSWORD was found (checked "
        f"{input_file.parent / '.env'} and the current environment)."
    )


def iter_zip_member_lines(input_file: Path, password: Optional[str] = None) -> Iterator[bytes]:
    """Stream a zip archive's contents line-by-line via the `7z` CLI.

    Unlike pyzipper/fsspec, this never gives Dask a compressed stream to (fail
    to) split -- it shells out to `7z e -so` so the whole archive is never
    held in memory or written out decrypted to disk; only one line is ever
    in flight at a time. Used for large AES-encrypted or plain zip archives
    where extracting the full decrypted content isn't an option.
    """
    cmd = ["7z", "e", "-so"]
    if password:
        cmd.append(f"-p{password}")
    cmd.append(str(input_file))

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for raw_line in proc.stdout:
            line = raw_line.strip()
            if line:
                yield line
    finally:
        proc.stdout.close()
        stderr_output = proc.stderr.read()
        proc.stderr.close()
        returncode = proc.wait()
        if returncode != 0:
            raise RuntimeError(
                f"7z extraction of {input_file!r} failed (exit {returncode}): "
                f"{stderr_output.decode(errors='replace').strip()}"
            )
