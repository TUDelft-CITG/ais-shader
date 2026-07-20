import os
import zipfile
from pathlib import Path

import fsspec.compression
import pyzipper
from dotenv import load_dotenv

AESZIP_CODEC_NAME = "aeszip"


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


def _open_aeszip(infile, mode="rb", password: bytes = b"", **kwargs):
    z = pyzipper.AESZipFile(infile)
    z.setpassword(password)
    name = z.namelist()[0]
    return z.open(name, mode="r")


def register_aeszip_codec(password: bytes) -> None:
    """Register the "aeszip" fsspec compression codec with a bound password.

    Must be called both in the process building the dask graph and (via
    Client.run) on every worker process, since fsspec's compression
    registry is in-memory per-process and workers don't inherit it.
    """
    import functools

    callback = functools.partial(_open_aeszip, password=password)
    fsspec.compression.register_compression(AESZIP_CODEC_NAME, callback, [], force=True)
