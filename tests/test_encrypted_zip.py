import json

import geopandas as gpd
import pyzipper

from ais_shader.preprocessing import run_ndjson_conversion


def test_ndjson_conversion_from_encrypted_zip(tmp_path, monkeypatch):
    ndjson_name = "test.ndjson"
    archive_file = tmp_path / "test.7z"  # mislabeled extension, like the real RWS files
    parquet_file = tmp_path / "test.parquet"
    password = "s3cr3t"

    records = [
        {
            "id": "1",
            "track_id": "vessel-1",
            "timestamp": "2026-07-05T12:00:00Z",
            "longitude": {"value": 4.5},
            "latitude": {"value": 52.1},
            "cog": {"value": 180.0},
            "sog": {"value": 12.5},
            "heading": {"code": "179"},
            "beam": {"value": 15.0},
            "length": {"value": 100.0},
            "draught": {"value": 5.5},
            "status": {"code": "3"},
            "shiptypeAIS": {"code": "70"},
        },
    ]
    ndjson_bytes = "\n".join(json.dumps(r) for r in records).encode() + b"\n"

    with pyzipper.AESZipFile(
        archive_file, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
    ) as z:
        z.setpassword(password.encode())
        z.writestr(ndjson_name, ndjson_bytes)

    monkeypatch.setenv("TESTDATA_PASSWORD", password)

    run_ndjson_conversion(archive_file, parquet_file, scheduler=None)

    assert parquet_file.exists()
    gdf = gpd.read_parquet(parquet_file)
    assert len(gdf) == 1
    assert gdf.iloc[0]["mmsi"] == "vessel-1"


def test_ndjson_conversion_from_encrypted_zip_aggregated_profile(tmp_path, monkeypatch):
    # RWS "aggregated" ndjson profile, nested under "data". Identifiers are
    # fabricated, not real vessel data.
    ndjson_name = "test.ndjson"
    archive_file = tmp_path / "test.7z"
    parquet_file = tmp_path / "test.parquet"
    password = "s3cr3t"

    records = [
        {
            "id": "1",
            "mmsi": "111111111",
            "track_id": "00000000-0000-0000-0000-000000000001",
            "timestamp": "2026-06-08T08:00:01.102Z",
            "data": {
                "cog": {"value": 27.5},
                "sog": {"value": 3.6},
                "heading": {"value": 39},
                "status": {"code": "3"},
                "latitude": {"value": 53.5712},
                "longitude": {"value": 6.07155},
                "casco": {
                    "to_bow": {"value": 24},
                    "to_stern": {"value": 13},
                    "to_port": {"value": 8},
                    "to_starboard": {"value": 1},
                    "draught": {"value": 4},
                },
                "identification": {
                    "imo": {"value": "0000001"},
                    "name": {"value": "TEST VESSEL"},
                    "callsign": {"value": "TEST1"},
                    "shiptypeAIS": {"code": "90"},
                },
            },
        },
    ]
    ndjson_bytes = "\n".join(json.dumps(r) for r in records).encode() + b"\n"

    with pyzipper.AESZipFile(
        archive_file, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
    ) as z:
        z.setpassword(password.encode())
        z.writestr(ndjson_name, ndjson_bytes)

    monkeypatch.setenv("TESTDATA_PASSWORD", password)

    run_ndjson_conversion(archive_file, parquet_file, scheduler=None)

    assert parquet_file.exists()
    gdf = gpd.read_parquet(parquet_file)
    assert len(gdf) == 1
    row = gdf.iloc[0]
    assert row["mmsi"] == "111111111"
    assert row["track_id"] == "00000000-0000-0000-0000-000000000001"
    assert row["imo"] == "0000001"
    assert row["name"] == "TEST VESSEL"
    assert row["callsign"] == "TEST1"
    assert row["shiptypeAIS"] == "90"
    assert not row.geometry.is_empty
