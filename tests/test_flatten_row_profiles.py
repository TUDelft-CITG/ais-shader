from ais_shader.preprocessing import flatten_row


def test_flatten_row_flat_profile():
    row = {
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
    }
    result = flatten_row(row)
    assert result["mmsi"] == "vessel-1"
    assert result["longitude"] == 4.5
    assert result["latitude"] == 52.1
    assert result["cog"] == 180.0
    assert result["beam"] == 15.0
    assert result["length"] == 100.0
    assert result["draught"] == 5.5
    assert result["status"] == "3"
    assert result["shiptypeAIS"] == "70"
    assert "imo" not in result
    assert "name" not in result
    assert "callsign" not in result


def test_flatten_row_aggregated_profile():
    # RWS "aggregated" ndjson profile: AIS fields nested under "data", with
    # beam/length derived from casco reference-point offsets and
    # identification (imo/name/callsign/shiptypeAIS) nested under identification.
    # mmsi and track_id are distinct top-level fields in this profile.
    # All identifiers below are fabricated, not real vessel data.
    row = {
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
                "name": {"value": "TEST VESSEL         "},
                "callsign": {"value": "TEST1  "},
                "shiptypeAIS": {"code": "90"},
            },
        },
    }
    result = flatten_row(row, include_identification=True)
    assert result["mmsi"] == "111111111"
    assert result["track_id"] == "00000000-0000-0000-0000-000000000001"
    assert result["imo"] == "0000001"
    assert result["name"] == "TEST VESSEL"
    assert result["callsign"] == "TEST1"
    assert result["longitude"] == 6.07155
    assert result["latitude"] == 53.5712
    assert result["cog"] == 27.5
    assert result["sog"] == 3.6
    assert result["heading"] == 39.0
    assert result["status"] == "3"
    assert result["shiptypeAIS"] == "90"
    # length = to_bow + to_stern, beam = to_port + to_starboard
    assert result["length"] == 37.0
    assert result["beam"] == 9.0
    assert result["draught"] == 4.0


def test_flatten_row_aggregated_profile_missing_casco():
    row = {
        "track_id": "vessel-3",
        "timestamp": "2026-06-08T08:00:01.102Z",
        "data": {
            "longitude": {"value": 6.07155},
            "latitude": {"value": 53.5712},
        },
    }
    result = flatten_row(row)
    assert result["beam"] is None
    assert result["length"] is None
    assert result["draught"] is None
    assert result["shiptypeAIS"] is None
