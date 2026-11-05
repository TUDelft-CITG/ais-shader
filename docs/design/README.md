# ais-colab design system

> Colour and type tokens for maps, figures and dashboards built on this pipeline. The machine-readable tokens are in [`tokens.json`](tokens.json); the colours are taken from `config.toml`, `scripts/generate_qgis_styles.py`, `docs/styles/*.qml` and the documentation figures. Keep them in sync when you change a style there.

A scalable Python pipeline to visualize AIS vessel tracks as tiled, high-quality maps.

ais-colab's visual language is a map's, not an app's: almost every colour here paints data (track density, vessel classes, encounter types) on top of a basemap, and the few interface colours exist to keep labels and figures legible over that data. These tokens come from the pipeline's renderer config, its generated QGIS layer styles and its documentation figures.

## Principles

- **Density first.** The signature image is a continuous density raster of vessel tracks. Low-density areas fade to transparent, so the basemap shows through and the shipping lanes carry the picture.
- **One hue per category, fixed everywhere.** A vessel class or encounter type has exactly one colour, used in every layer (trajectories, segments, stationary, encounters, CPA lines). Don't recolour a class per map.
- **Halo, don't box.** Points, markers and lines sit on a thin white casing (`map-halo`); labels sit on a slate text buffer (`label-buffer`). Nothing gets a filled panel.

## Colour

### Density ramps

Counts are drawn on a log scale, normalised by a robust global maximum so tiles join without seams.

- **Oslo** (`oslo-0` → `oslo-4`) is the default (`config.toml` → `style.colormap = "oslo"`). The renderer starts Crameri Oslo at 20% so the faintest traffic is a deep blue rather than black, and ramps alpha up from 0.2. The five tokens are samples of that ramp; the renderer uses the full 256-step colormap from `cmcrameri`.
- **Blues** (`blues-0` → `blues-8`), **dark** (`dark-ramp-*`) and **light** (`light-ramp-*`) are the QGIS raster styles for the same rasters: blues for neutral basemaps, the dark ramp (transparent near-black to white) over dark basemaps, the light ramp (transparent white through sepia to black) over light ones. Each starts fully transparent.

### Vessel groups

| Class | Token |
| --- | --- |
| Cargo | `vessel-cargo` |
| Commercial (aggregate) | `vessel-commercial` → alias of `vessel-cargo` |
| Tanker | `vessel-tanker` |
| Tug | `vessel-tug` |
| Passenger | `vessel-passenger` |
| Pleasure craft / sailing | `vessel-pleasure` |
| Fishing | `vessel-fishing` |
| Military | `vessel-military` |
| Other | `vessel-other` |
| Missing | `vessel-null` |

### Encounter types

`encounter-crossing`, `encounter-head-on`, `encounter-overtaking`, `encounter-parallel`, `encounter-stationary`, `encounter-null`. Encounter markers and the CPA lines that join the two ships both use these, over `map-halo`.

### Map features

- `map-point`: trajectorized AIS pings (course-rotated arrows).
- `map-fairway`: dashed fairway centerline and river-mile markers (deep nautical teal).
- `map-fairway-section`: Rijkswaterstaat FIS fairway sections.
- `map-halo`, `map-halo-marker`: white casings.

### Labels, figures and diagrams

- Map labels: `paper` text on a 0.8 mm `label-buffer`.
- Documentation figures are dark: `ink` background, `figure-grid` dashed gridlines, `figure-muted` axes and ticks, `paper` titles and legend text.
- Pipeline diagrams: data stores in `diagram-data-fill` / `diagram-data-stroke`, outputs in `diagram-artifact-fill` / `diagram-artifact-stroke`.

### Accessibility notes

- `paper` on `ink` is 17.9:1, `figure-muted` on `ink` 6.96:1, `diagram-data-stroke` on its fill 6.6:1.
- `diagram-artifact-stroke` on its fill is 3.5:1: fine for strokes and large type, not for small text.
- Several vessel classes are separated by hue more than lightness (cargo / passenger, tanker / pleasure). Where colour-blind readers matter, pair the legend with direct labels.
- The data colours are not text colours.

## Typography

Two layers. The **console type** gives dashboards and VTS-style displays their technical, operator-room feel. The **source type** is what the pipeline itself renders into maps and figures.

### Console type (dashboards, VTS displays)

All three faces are hosted on Google Fonts (SIL Open Font License), so there are no font files to ship.

- **Barlow Condensed** (`--font-display`): screen titles, panel titles and uppercase overlines. Narrow and DIN-like, it reads like signage on a ship's bridge and fits long fairway and vessel names in tight headers.
- **Barlow** (`--font-ui`): running text, rows, captions. It comes from the same family, so the condensed headings and the body sit together.
- **IBM Plex Mono** (`--font-mono`): every value that comes from a sensor or the data, such as MMSI, SOG, COG, CPA/TCPA, coordinates and timestamps. A monospace with tabular figures keeps columns aligned and stops digits jumping when a value updates.

Rules:

- **Labels in sans, values in mono.** A field reads as `section-label` or `caption` for the name, then `readout` for the value: `SOG` → `12.4 kn`.
- Overlines (`section-label`) are uppercase and tracked at 0.12em. Never set body text in uppercase.
- Turn on `font-variant-numeric: tabular-nums` for all numbers. Right-align numeric columns. Always give the unit (kn, °, m, nm) in the same mono face, after a thin space.
- Times are ISO dates in 24-hour UTC unless the view states another zone.
- On dark consoles, set text in `paper` on `ink`, with secondary text in `figure-muted` (6.96:1). Don't put readouts directly on density rasters; put them on `label-buffer`.

| Style | Face | Size / line | Weight |
| --- | --- | --- | --- |
| `display` | Barlow Condensed | 48 / 52 | 600 |
| `panel-title` | Barlow Condensed | 20 / 24 | 600 |
| `section-label` | Barlow Condensed, uppercase | 12 / 16 | 600 |
| `body`, `body-strong` | Barlow | 14 / 20 | 400, 600 |
| `caption` | Barlow | 12 / 16 | 500 |
| `readout-lg` | IBM Plex Mono | 32 / 36 | 500 |
| `readout` | IBM Plex Mono | 14 / 20 | 400 |
| `coordinate`, `timestamp` | IBM Plex Mono | 12 / 16 | 400, 500 |

### Source type (maps and figures)

The pipeline names no typeface. QGIS labels ask for the generic `Sans-Serif` and figures use matplotlib's default, so these styles use plain `sans-serif` (`--font-sans`). Sizes were set in points in the source and are converted at 1 pt = 1.333 px:

- Map labels: 7 pt, bold for river miles (`label-mile-marker`), regular for fairway sections (`label-fairway`).
- Figures: 14 pt bold titles (`figure-title`), 9 pt ticks and legends (`figure-tick`), 6 pt bold patch labels (`figure-annotation`).

To bring the generated maps in line with the console, the QGIS styles could switch their label font to Barlow Condensed. That change belongs in `generate_qgis_styles.py`.

## Scope

Synced from `main@808a70b`. Only colour and type tokens are included.

- The `bin-symbology.qml` ramp (matplotlib magma, 50 stops) was not copied; that QGIS project uses it as-is.
- No line heights for the source styles: the source doesn't set any. The console styles were added by hand afterwards and are not from the repository.
- Not brought in: spacing, radii, font files (none in the repository), logos or images, and components. The repository is a Python pipeline with no UI component library.
