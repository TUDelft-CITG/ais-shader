"""Generate a Markdown (+ HTML) overview report for a trajectorized AIS lines GeoParquet file.

Usage:
    uv run python scripts/report_overview.py <path-to-lines.geoparquet> [output.md]
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import markdown
import pandas as pd

VESSEL_GROUPS_JSON = Path(__file__).resolve().parents[1] / "resources" / "vessel_groups.json"
PIPELINE_SVG_PLACEHOLDER = "{{PIPELINE_SVG}}"


def load_vessel_type_descriptions(path: Path = VESSEL_GROUPS_JSON) -> dict:
    entries = json.loads(path.read_text())
    return {entry["vessel_code"]: entry["vessel_type"] for entry in entries}


def build_pipeline_d2(exclude_moored: bool, gap_threshold_hours: float) -> str:
    gap_minutes = gap_threshold_hours * 60
    compute_label = f"trajectory compute\\n(segmentatie, gap-threshold={gap_minutes:.0f} min)"
    if exclude_moored:
        compute_label += "\\n+ --exclude-moored (status=5 eruit gefilterd)"

    return f"""direction: down
preprocess: "preprocess\\n(ruwe AIS -> geoparquet)"
compute: "{compute_label}"
linestring: "trajectory to-linestring\\n(punten -> lines per trip)"
report: "report_overview.py\\n(dit rapport)"

preprocess -> compute -> linestring -> report
"""


def render_d2_svg(d2_source: str, d2_path: Path, svg_path: Path) -> bool:
    d2_path.write_text(d2_source)
    try:
        subprocess.run(["d2", str(d2_path), str(svg_path)], check=True, capture_output=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"Warning: could not render pipeline diagram with d2 ({exc}); "
              "the HTML report will only show the d2 source.", file=sys.stderr)
        return False


def build_report(df: pd.DataFrame, exclude_moored: bool, gap_threshold_hours: float) -> str:
    n_trips = len(df)
    n_ships = df["MMSI"].nunique()

    months = df["TrackStartTime"].dt.to_period("M")
    per_month_unique = df.groupby(months)["MMSI"].nunique()
    per_month_unique.index = per_month_unique.index.astype(str)

    trips_per_ship = df.groupby("MMSI").size()

    length_summary = df["Length"].describe().to_frame(name="Lengte (m)")
    length_summary.loc["ontbrekend"] = df["Length"].isna().sum()

    vessel_groups = df["VesselGroup"].value_counts().to_frame(name="Trips")

    type_descriptions = load_vessel_type_descriptions()

    def describe_type(code):
        try:
            return type_descriptions.get(int(code), "Onbekend")
        except (TypeError, ValueError):
            return "Onbekend"

    other = df[df["VesselGroup"] == "Other"]
    other_detail = other["VesselType"].value_counts().to_frame(name="Trips")
    other_detail.insert(0, "Beschrijving", [describe_type(code) for code in other_detail.index])
    other_detail.index.name = "VesselType-code"
    other_detail["Unieke schepen"] = other.groupby("VesselType")["MMSI"].nunique()

    lines = []
    lines.append("# Overzichtsrapport AIS-trajecten")
    lines.append("")

    if exclude_moored:
        lines.append("> Afgemeerde schepen (navigational status = moored, code 5) zijn vóór de trip-segmentatie "
                      "uitgefilterd (`trajectory compute --exclude-moored`). Zonder deze filter genereren "
                      "permanent afgemeerde schepen door GPS-jitter duizenden nep-trips en vertekenen ze de "
                      "trip-statistieken hieronder.")
        lines.append("")

    lines.append("## Samenvatting")
    lines.append("")
    lines.append(f"- Schepen (unieke MMSI, hele periode): **{n_ships}**")
    lines.append(f"- Trips (trajectsegmenten): **{n_trips}**")
    lines.append(f"- Trips per schip - mediaan / gemiddelde / max: "
                 f"{trips_per_ship.median():.0f} / {trips_per_ship.mean():.1f} / {trips_per_ship.max()}")
    lines.append("")

    lines.append("## Unieke schepen per maand")
    lines.append("")
    lines.append(f"De som van de unieke aantallen per maand is {per_month_unique.sum()}, tegenover {n_ships} "
                  f"unieke schepen over de hele periode. Het verschil ({per_month_unique.sum() - n_ships}) komt "
                  "doordat schepen in meerdere maanden actief zijn, niet door hergebruikte MMSI-hashes.")
    lines.append("")
    lines.append(per_month_unique.to_frame(name="Unieke schepen").to_markdown())
    lines.append("")

    lines.append("## Lengteverdeling schepen (m)")
    lines.append("")
    lines.append(length_summary.to_markdown(floatfmt=".1f"))
    lines.append("")

    lines.append("## Verdeling scheepstype (op aantal trips)")
    lines.append("")
    lines.append(vessel_groups.to_markdown())
    lines.append("")

    lines.append("## Detail groep 'Other'")
    lines.append("")
    lines.append(other_detail.to_markdown())
    lines.append("")

    lines.append("## Pipeline")
    lines.append("")
    lines.append("```d2")
    lines.append(build_pipeline_d2(exclude_moored, gap_threshold_hours).rstrip("\n"))
    lines.append("```")
    lines.append("")
    lines.append(PIPELINE_SVG_PLACEHOLDER)
    lines.append("")

    return "\n".join(lines)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_file", type=Path, help="Path to the -lines.geoparquet file.")
    parser.add_argument("output_md", type=Path, nargs="?", default=None,
                         help="Output Markdown path. Defaults to <input>.report.md.")
    parser.add_argument("--exclude-moored", dest="exclude_moored", action="store_true", default=None,
                         help="Report that moored (status=5) points were filtered out upstream. "
                              "Defaults to auto-detect from the filename ('nomoor'/'exclude-moored').")
    parser.add_argument("--include-moored", dest="exclude_moored", action="store_false",
                         help="Report that moored points were NOT filtered out.")
    parser.add_argument("--gap-threshold-hours", type=float, default=0.25,
                         help="Gap threshold (in hours) used upstream in 'trajectory compute', "
                              "shown in the pipeline diagram. Defaults to 0.25 (15 min).")
    return parser.parse_args()


def main():
    args = parse_args()

    input_path = args.input_file
    output_md = args.output_md or input_path.with_suffix(".report.md")
    output_html = output_md.with_suffix(".html")

    exclude_moored = args.exclude_moored
    if exclude_moored is None:
        exclude_moored = "nomoor" in input_path.stem.lower() or "exclude-moored" in input_path.stem.lower()

    df = gpd.read_parquet(input_path)
    report_md = build_report(df, exclude_moored, args.gap_threshold_hours)

    output_md.write_text(report_md)

    d2_path = output_md.parent / "pipeline.d2"
    svg_path = output_md.parent / "pipeline.svg"
    svg_rendered = render_d2_svg(build_pipeline_d2(exclude_moored, args.gap_threshold_hours), d2_path, svg_path)

    html_body = markdown.markdown(report_md, extensions=["tables", "fenced_code"])
    if svg_rendered:
        svg_content = svg_path.read_text()
        html_body = html_body.replace(f"<p>{PIPELINE_SVG_PLACEHOLDER}</p>", svg_content)

    html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{input_path.name} - Overzichtsrapport</title>
<style>
body {{ font-family: sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }}
table {{ border-collapse: collapse; margin-bottom: 1.5rem; }}
th, td {{ border: 1px solid #ccc; padding: 0.4rem 0.8rem; text-align: right; }}
th:first-child, td:first-child {{ text-align: left; }}
blockquote {{ border-left: 4px solid #f0ad4e; margin: 0 0 1.5rem; padding: 0.5rem 1rem; background: #fff8ec; }}
pre {{ background: #f5f5f5; padding: 0.75rem; overflow-x: auto; }}
svg {{ max-width: 100%; height: auto; }}
</style>
</head>
<body>
{html_body}
</body>
</html>
"""
    output_html.write_text(html)

    print(f"Wrote {output_md}")
    print(f"Wrote {output_html}")
    if svg_rendered:
        print(f"Wrote {svg_path}")


if __name__ == "__main__":
    main()
