"""Generate consistent, production-quality QGIS QML styles for AIS layers."""

import os
import shutil
import uuid

STYLES_DIR = "/home/fbaart/src/ais-shader/docs/styles"
DATA_DIR = "/scratch-shared/fbaart/data/mississippi_1h"

def uid():
    return f"{{{uuid.uuid4()}}}"

# ---------------------------------------------------------------------------
# 1. trajectorized_points.qml
# Arrow marker scaled in meters (by length) rotated by cog/heading,
# with high saturation (~0.8), subtle thin white outline.
# ---------------------------------------------------------------------------
TRAJECTORIZED_POINTS_QML = f"""<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis layerType="Vector" styleCategories="Symbology|Temporal" version="4.2.1-Belém do Pará">
  <temporal accumulate="0" durationField="mmsi" durationUnit="min" enabled="1" endExpression="" endField="" fixedDuration="1" limitMode="0" mode="1" startExpression="" startField="base_date_time">
    <fixedRange>
      <start></start>
      <end></end>
    </fixedRange>
  </temporal>
  <renderer-v2 enableorderby="0" forceraster="0" referencescale="-1" symbollevels="0" type="singleSymbol">
    <symbols>
      <symbol alpha="1" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0" name="0" type="marker">
        <data_defined_properties>
          <Option type="Map">
            <Option name="name" type="QString" value=""/>
            <Option name="properties"/>
            <Option name="type" type="QString" value="collection"/>
          </Option>
        </data_defined_properties>
        <layer class="SimpleMarker" enabled="1" id="{uid()}" locked="0" pass="0">
          <Option type="Map">
            <Option name="angle" type="QString" value="0"/>
            <Option name="cap_style" type="QString" value="round"/>
            <Option name="color" type="QString" value="37,99,235,240,hsv:0.61388889,0.84313725,0.92156863,0.94117647"/>
            <Option name="horizontal_anchor_point" type="QString" value="1"/>
            <Option name="joinstyle" type="QString" value="round"/>
            <Option name="name" type="QString" value="arrow"/>
            <Option name="offset" type="QString" value="0,0"/>
            <Option name="offset_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="offset_unit" type="QString" value="MM"/>
            <Option name="outline_color" type="QString" value="255,255,255,235,rgb:1,1,1,0.92156863"/>
            <Option name="outline_style" type="QString" value="solid"/>
            <Option name="outline_width" type="QString" value="0.35"/>
            <Option name="outline_width_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="outline_width_unit" type="QString" value="MM"/>
            <Option name="scale_method" type="QString" value="diameter"/>
            <Option name="size" type="QString" value="25"/>
            <Option name="size_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="size_unit" type="QString" value="RenderMetersInMapUnits"/>
            <Option name="vertical_anchor_point" type="QString" value="1"/>
          </Option>
          <data_defined_properties>
            <Option type="Map">
              <Option name="name" type="QString" value=""/>
              <Option name="properties" type="Map">
                <Option name="angle" type="Map">
                  <Option name="active" type="bool" value="true"/>
                  <Option name="expression" type="QString" value="coalesce(&quot;heading&quot;, &quot;cog&quot;, 0)"/>
                  <Option name="type" type="int" value="3"/>
                </Option>
                <Option name="size" type="Map">
                  <Option name="active" type="bool" value="true"/>
                  <Option name="expression" type="QString" value="coalesce(&quot;length&quot;, 25)"/>
                  <Option name="type" type="int" value="3"/>
                </Option>
              </Option>
              <Option name="type" type="QString" value="collection"/>
            </Option>
          </data_defined_properties>
        </layer>
      </symbol>
    </symbols>
    <rotation/>
    <sizescale/>
    <data-defined-properties>
      <Option type="Map">
        <Option name="name" type="QString" value=""/>
        <Option name="properties"/>
        <Option name="type" type="QString" value="collection"/>
      </Option>
    </data-defined-properties>
  </renderer-v2>
  <selection mode="Default">
    <selectionColor invalid="1"/>
  </selection>
  <blendMode>0</blendMode>
  <featureBlendMode>0</featureBlendMode>
  <layerGeometryType>0</layerGeometryType>
</qgis>
"""

# ---------------------------------------------------------------------------
# 2. encounters.qml
# Point marker categorized by encounter_type with ~0.8 saturation and subtle
# crisp thin white outline around each circle.
# ---------------------------------------------------------------------------
ENCOUNTERS_PALETTE = [
    ("crossing", "124,67,238,255,hsv:0.72222221,0.71764708,0.93333334,1"),
    ("head-on", "212,143,108,255,hsv:0.05555556,0.49019608,0.83137256,1"),
    ("overtaking", "208,29,148,255,hsv:0.88888890,0.85882354,0.81568629,1"),
    ("parallel_sailing", "83,233,133,255,hsv:0.38888890,0.64313728,0.91372550,1"),
    ("stationary", "186,215,129,255,hsv:0.22222222,0.40000001,0.84313726,1"),
    ("NULL", "128,191,223,255,hsv:0.55555558,0.42745098,0.87450981,1"),
]

def make_encounters_qml():
    categories_xml = []
    symbols_xml = []
    for idx, (label, color_val) in enumerate(ENCOUNTERS_PALETTE):
        type_attr = "NULL" if label == "NULL" else "string"
        value_attr = "NULL" if label == "NULL" else label
        lbl = "" if label == "NULL" else label
        categories_xml.append(f'      <category label="{lbl}" render="true" symbol="{idx}" type="{type_attr}" uuid="{uid()}" value="{value_attr}"/>')
        symbols_xml.append(f"""      <symbol alpha="1" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0" name="{idx}" type="marker">
        <data_defined_properties>
          <Option type="Map">
            <Option name="name" type="QString" value=""/>
            <Option name="properties"/>
            <Option name="type" type="QString" value="collection"/>
          </Option>
        </data_defined_properties>
        <layer class="SimpleMarker" enabled="1" id="{uid()}" locked="0" pass="0">
          <Option type="Map">
            <Option name="angle" type="QString" value="0"/>
            <Option name="cap_style" type="QString" value="square"/>
            <Option name="color" type="QString" value="{color_val}"/>
            <Option name="horizontal_anchor_point" type="QString" value="1"/>
            <Option name="joinstyle" type="QString" value="bevel"/>
            <Option name="name" type="QString" value="circle"/>
            <Option name="offset" type="QString" value="0,0"/>
            <Option name="offset_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="offset_unit" type="QString" value="MM"/>
            <Option name="outline_color" type="QString" value="255,255,255,235,rgb:1,1,1,0.92156863"/>
            <Option name="outline_style" type="QString" value="solid"/>
            <Option name="outline_width" type="QString" value="0.35"/>
            <Option name="outline_width_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="outline_width_unit" type="QString" value="MM"/>
            <Option name="scale_method" type="QString" value="diameter"/>
            <Option name="size" type="QString" value="3"/>
            <Option name="size_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="size_unit" type="QString" value="MM"/>
            <Option name="vertical_anchor_point" type="QString" value="1"/>
          </Option>
          <data_defined_properties>
            <Option type="Map">
              <Option name="name" type="QString" value=""/>
              <Option name="properties"/>
              <Option name="type" type="QString" value="collection"/>
            </Option>
          </data_defined_properties>
        </layer>
      </symbol>""")

    cat_str = "\n".join(categories_xml)
    sym_str = "\n".join(symbols_xml)
    return f"""<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis layerType="Vector" styleCategories="Symbology|Temporal" version="4.2.1-Belém do Pará">
  <temporal accumulate="0" durationField="encounter_id" durationUnit="min" enabled="1" endExpression="" endField="end_time" fixedDuration="0" limitMode="0" mode="2" startExpression="" startField="start_time">
    <fixedRange>
      <start></start>
      <end></end>
    </fixedRange>
  </temporal>
  <renderer-v2 attr="encounter_type" enableorderby="0" forceraster="0" referencescale="-1" symbollevels="0" type="categorizedSymbol">
    <categories>
{cat_str}
    </categories>
    <symbols>
{sym_str}
    </symbols>
    <rotation/>
    <sizescale/>
    <data-defined-properties>
      <Option type="Map">
        <Option name="name" type="QString" value=""/>
        <Option name="properties"/>
        <Option name="type" type="QString" value="collection"/>
      </Option>
    </data-defined-properties>
  </renderer-v2>
  <selection mode="Default">
    <selectionColor invalid="1"/>
  </selection>
  <blendMode>0</blendMode>
  <featureBlendMode>0</featureBlendMode>
  <layerGeometryType>0</layerGeometryType>
</qgis>
"""

# ---------------------------------------------------------------------------
# 3. timeseries.qml
# LineString connecting the two ships, categorized by encounter_type.
# Two layers per symbol:
#   Layer 0: subtle semi-transparent white casing (halo)
#   Layer 1: vibrant categorized line with ~0.8 saturation
# Temporal mode=1, startField=timestamp, fixedDuration=30s
# ---------------------------------------------------------------------------
def make_timeseries_qml():
    categories_xml = []
    symbols_xml = []
    for idx, (label, color_val) in enumerate(ENCOUNTERS_PALETTE):
        type_attr = "NULL" if label == "NULL" else "string"
        value_attr = "NULL" if label == "NULL" else label
        lbl = "" if label == "NULL" else label
        categories_xml.append(f'      <category label="{lbl}" render="true" symbol="{idx}" type="{type_attr}" uuid="{uid()}" value="{value_attr}"/>')
        symbols_xml.append(f"""      <symbol alpha="1" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0" name="{idx}" type="line">
        <data_defined_properties>
          <Option type="Map">
            <Option name="name" type="QString" value=""/>
            <Option name="properties"/>
            <Option name="type" type="QString" value="collection"/>
          </Option>
        </data_defined_properties>
        <!-- Outer white halo casing -->
        <layer class="SimpleLine" enabled="1" id="{uid()}" locked="0" pass="0">
          <Option type="Map">
            <Option name="align_dash_pattern" type="QString" value="0"/>
            <Option name="capstyle" type="QString" value="round"/>
            <Option name="customdash" type="QString" value="5;2"/>
            <Option name="customdash_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="customdash_unit" type="QString" value="MM"/>
            <Option name="draw_inside_polygon" type="QString" value="0"/>
            <Option name="joinstyle" type="QString" value="round"/>
            <Option name="line_color" type="QString" value="255,255,255,210,rgb:1,1,1,0.82352941"/>
            <Option name="line_style" type="QString" value="solid"/>
            <Option name="line_width" type="QString" value="0.85"/>
            <Option name="line_width_unit" type="QString" value="MM"/>
            <Option name="offset" type="QString" value="0"/>
            <Option name="offset_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="offset_unit" type="QString" value="MM"/>
            <Option name="ring_filter" type="QString" value="0"/>
            <Option name="trim_distance_end" type="QString" value="0"/>
            <Option name="trim_distance_end_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="trim_distance_end_unit" type="QString" value="MM"/>
            <Option name="trim_distance_start" type="QString" value="0"/>
            <Option name="trim_distance_start_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="trim_distance_start_unit" type="QString" value="MM"/>
            <Option name="tweak_dash_pattern_on_corners" type="QString" value="0"/>
            <Option name="use_custom_dash" type="QString" value="0"/>
            <Option name="width_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
          </Option>
          <data_defined_properties>
            <Option type="Map">
              <Option name="name" type="QString" value=""/>
              <Option name="properties"/>
              <Option name="type" type="QString" value="collection"/>
            </Option>
          </data_defined_properties>
        </layer>
        <!-- Inner colored proximity line -->
        <layer class="SimpleLine" enabled="1" id="{uid()}" locked="0" pass="1">
          <Option type="Map">
            <Option name="align_dash_pattern" type="QString" value="0"/>
            <Option name="capstyle" type="QString" value="round"/>
            <Option name="customdash" type="QString" value="5;2"/>
            <Option name="customdash_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="customdash_unit" type="QString" value="MM"/>
            <Option name="draw_inside_polygon" type="QString" value="0"/>
            <Option name="joinstyle" type="QString" value="round"/>
            <Option name="line_color" type="QString" value="{color_val}"/>
            <Option name="line_style" type="QString" value="solid"/>
            <Option name="line_width" type="QString" value="0.45"/>
            <Option name="line_width_unit" type="QString" value="MM"/>
            <Option name="offset" type="QString" value="0"/>
            <Option name="offset_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="offset_unit" type="QString" value="MM"/>
            <Option name="ring_filter" type="QString" value="0"/>
            <Option name="trim_distance_end" type="QString" value="0"/>
            <Option name="trim_distance_end_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="trim_distance_end_unit" type="QString" value="MM"/>
            <Option name="trim_distance_start" type="QString" value="0"/>
            <Option name="trim_distance_start_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="trim_distance_start_unit" type="QString" value="MM"/>
            <Option name="tweak_dash_pattern_on_corners" type="QString" value="0"/>
            <Option name="use_custom_dash" type="QString" value="0"/>
            <Option name="width_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
          </Option>
          <data_defined_properties>
            <Option type="Map">
              <Option name="name" type="QString" value=""/>
              <Option name="properties"/>
              <Option name="type" type="QString" value="collection"/>
            </Option>
          </data_defined_properties>
        </layer>
      </symbol>""")

    cat_str = "\n".join(categories_xml)
    sym_str = "\n".join(symbols_xml)
    return f"""<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis layerType="Vector" styleCategories="Symbology|Temporal" version="4.2.1-Belém do Pará">
  <temporal accumulate="0" durationField="distance_m" durationUnit="min" enabled="1" endExpression="" endField="" fixedDuration="30" limitMode="0" mode="1" startExpression="" startField="timestamp">
    <fixedRange>
      <start></start>
      <end></end>
    </fixedRange>
  </temporal>
  <renderer-v2 attr="encounter_type" enableorderby="0" forceraster="0" referencescale="-1" symbollevels="1" type="categorizedSymbol">
    <categories>
{cat_str}
    </categories>
    <symbols>
{sym_str}
    </symbols>
    <rotation/>
    <sizescale/>
    <data-defined-properties>
      <Option type="Map">
        <Option name="name" type="QString" value=""/>
        <Option name="properties"/>
        <Option name="type" type="QString" value="collection"/>
      </Option>
    </data-defined-properties>
  </renderer-v2>
  <selection mode="Default">
    <selectionColor invalid="1"/>
  </selection>
  <blendMode>0</blendMode>
  <featureBlendMode>0</featureBlendMode>
  <layerGeometryType>1</layerGeometryType>
</qgis>
"""

# ---------------------------------------------------------------------------
# 4. trajectories.qml & stationary.qml
# LineString categorized by VesselGroup with harmonious ~0.8 saturation tones
# and subtle white casing.
# ---------------------------------------------------------------------------
VESSEL_GROUPS = [
    ("Cargo", "30,136,229,255,hsv:0.578,0.869,0.898,1"),           # Rich Azure
    ("Tanker", "216,27,96,255,hsv:0.939,0.875,0.847,1"),           # Deep Crimson Rose
    ("Tug", "251,140,0,255,hsv:0.093,0.940,0.984,1"),             # Vibrant Amber / Tug Orange
    ("Passenger", "0,172,193,255,hsv:0.518,1.000,0.757,1"),        # Turquoise / Cyan
    ("Pleasure Craft/Sailing", "142,36,170,255,hsv:0.789,0.788,0.667,1"), # Violet
    ("Fishing", "67,160,71,255,hsv:0.340,0.581,0.627,1"),          # Forest Green
    ("Military", "84,110,122,255,hsv:0.553,0.311,0.478,1"),        # Slate Navy
    ("Other", "120,144,156,255,hsv:0.556,0.231,0.612,1"),         # Muted Blue-Grey
    ("NULL", "158,158,158,255,hsv:0.0,0.0,0.62,1"),
]

def make_vesselgroup_line_qml(layer_type_name, start_field, end_field, line_width=0.45, casing_width=0.85):
    categories_xml = []
    symbols_xml = []
    for idx, (label, color_val) in enumerate(VESSEL_GROUPS):
        type_attr = "NULL" if label == "NULL" else "string"
        value_attr = "NULL" if label == "NULL" else label
        lbl = "" if label == "NULL" else label
        categories_xml.append(f'      <category label="{lbl}" render="true" symbol="{idx}" type="{type_attr}" uuid="{uid()}" value="{value_attr}"/>')
        symbols_xml.append(f"""      <symbol alpha="1" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0" name="{idx}" type="line">
        <data_defined_properties>
          <Option type="Map">
            <Option name="name" type="QString" value=""/>
            <Option name="properties"/>
            <Option name="type" type="QString" value="collection"/>
          </Option>
        </data_defined_properties>
        <!-- Subtle thin white outline casing -->
        <layer class="SimpleLine" enabled="1" id="{uid()}" locked="0" pass="0">
          <Option type="Map">
            <Option name="align_dash_pattern" type="QString" value="0"/>
            <Option name="capstyle" type="QString" value="round"/>
            <Option name="customdash" type="QString" value="5;2"/>
            <Option name="customdash_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="customdash_unit" type="QString" value="MM"/>
            <Option name="draw_inside_polygon" type="QString" value="0"/>
            <Option name="joinstyle" type="QString" value="round"/>
            <Option name="line_color" type="QString" value="255,255,255,210,rgb:1,1,1,0.82352941"/>
            <Option name="line_style" type="QString" value="solid"/>
            <Option name="line_width" type="QString" value="{casing_width}"/>
            <Option name="line_width_unit" type="QString" value="MM"/>
            <Option name="offset" type="QString" value="0"/>
            <Option name="offset_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="offset_unit" type="QString" value="MM"/>
            <Option name="ring_filter" type="QString" value="0"/>
            <Option name="trim_distance_end" type="QString" value="0"/>
            <Option name="trim_distance_end_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="trim_distance_end_unit" type="QString" value="MM"/>
            <Option name="trim_distance_start" type="QString" value="0"/>
            <Option name="trim_distance_start_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="trim_distance_start_unit" type="QString" value="MM"/>
            <Option name="tweak_dash_pattern_on_corners" type="QString" value="0"/>
            <Option name="use_custom_dash" type="QString" value="0"/>
            <Option name="width_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
          </Option>
          <data_defined_properties>
            <Option type="Map">
              <Option name="name" type="QString" value=""/>
              <Option name="properties"/>
              <Option name="type" type="QString" value="collection"/>
            </Option>
          </data_defined_properties>
        </layer>
        <!-- High saturation line -->
        <layer class="SimpleLine" enabled="1" id="{uid()}" locked="0" pass="1">
          <Option type="Map">
            <Option name="align_dash_pattern" type="QString" value="0"/>
            <Option name="capstyle" type="QString" value="round"/>
            <Option name="customdash" type="QString" value="5;2"/>
            <Option name="customdash_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="customdash_unit" type="QString" value="MM"/>
            <Option name="draw_inside_polygon" type="QString" value="0"/>
            <Option name="joinstyle" type="QString" value="round"/>
            <Option name="line_color" type="QString" value="{color_val}"/>
            <Option name="line_style" type="QString" value="solid"/>
            <Option name="line_width" type="QString" value="{line_width}"/>
            <Option name="line_width_unit" type="QString" value="MM"/>
            <Option name="offset" type="QString" value="0"/>
            <Option name="offset_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="offset_unit" type="QString" value="MM"/>
            <Option name="ring_filter" type="QString" value="0"/>
            <Option name="trim_distance_end" type="QString" value="0"/>
            <Option name="trim_distance_end_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="trim_distance_end_unit" type="QString" value="MM"/>
            <Option name="trim_distance_start" type="QString" value="0"/>
            <Option name="trim_distance_start_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="trim_distance_start_unit" type="QString" value="MM"/>
            <Option name="tweak_dash_pattern_on_corners" type="QString" value="0"/>
            <Option name="use_custom_dash" type="QString" value="0"/>
            <Option name="width_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
          </Option>
          <data_defined_properties>
            <Option type="Map">
              <Option name="name" type="QString" value=""/>
              <Option name="properties"/>
              <Option name="type" type="QString" value="collection"/>
            </Option>
          </data_defined_properties>
        </layer>
      </symbol>""")

    cat_str = "\n".join(categories_xml)
    sym_str = "\n".join(symbols_xml)
    return f"""<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis layerType="Vector" styleCategories="Symbology|Temporal" version="4.2.1-Belém do Pará">
  <temporal accumulate="0" durationField="DurationMinutes" durationUnit="min" enabled="1" endExpression="" endField="{end_field}" fixedDuration="0" limitMode="0" mode="2" startExpression="" startField="{start_field}">
    <fixedRange>
      <start></start>
      <end></end>
    </fixedRange>
  </temporal>
  <renderer-v2 attr="VesselGroup" enableorderby="0" forceraster="0" referencescale="-1" symbollevels="1" type="categorizedSymbol">
    <categories>
{cat_str}
    </categories>
    <symbols>
{sym_str}
    </symbols>
    <rotation/>
    <sizescale/>
    <data-defined-properties>
      <Option type="Map">
        <Option name="name" type="QString" value=""/>
        <Option name="properties"/>
        <Option name="type" type="QString" value="collection"/>
      </Option>
    </data-defined-properties>
  </renderer-v2>
  <selection mode="Default">
    <selectionColor invalid="1"/>
  </selection>
  <blendMode>0</blendMode>
  <featureBlendMode>0</featureBlendMode>
  <layerGeometryType>1</layerGeometryType>
</qgis>
"""

# ---------------------------------------------------------------------------
# 4b. segments.qml
# LineString segments with 15min trailing tail temporal expression,
# categorized by VesselGroup with subtle white casing.
# ---------------------------------------------------------------------------
def make_segments_qml(line_width=0.40, casing_width=0.80):
    categories_xml = []
    symbols_xml = []
    for idx, (label, color_val) in enumerate(VESSEL_GROUPS):
        type_attr = "NULL" if label == "NULL" else "string"
        value_attr = "NULL" if label == "NULL" else label
        lbl = "" if label == "NULL" else label
        categories_xml.append(f'      <category label="{lbl}" render="true" symbol="{idx}" type="{type_attr}" uuid="{uid()}" value="{value_attr}"/>')
        symbols_xml.append(f"""      <symbol alpha="1" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0" name="{idx}" type="line">
        <data_defined_properties>
          <Option type="Map">
            <Option name="name" type="QString" value=""/>
            <Option name="properties"/>
            <Option name="type" type="QString" value="collection"/>
          </Option>
        </data_defined_properties>
        <!-- Subtle thin white outline casing -->
        <layer class="SimpleLine" enabled="1" id="{uid()}" locked="0" pass="0">
          <Option type="Map">
            <Option name="align_dash_pattern" type="QString" value="0"/>
            <Option name="capstyle" type="QString" value="round"/>
            <Option name="customdash" type="QString" value="5;2"/>
            <Option name="customdash_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="customdash_unit" type="QString" value="MM"/>
            <Option name="draw_inside_polygon" type="QString" value="0"/>
            <Option name="joinstyle" type="QString" value="round"/>
            <Option name="line_color" type="QString" value="255,255,255,210,rgb:1,1,1,0.82352941"/>
            <Option name="line_style" type="QString" value="solid"/>
            <Option name="line_width" type="QString" value="{casing_width}"/>
            <Option name="line_width_unit" type="QString" value="MM"/>
            <Option name="offset" type="QString" value="0"/>
            <Option name="offset_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="offset_unit" type="QString" value="MM"/>
            <Option name="ring_filter" type="QString" value="0"/>
            <Option name="trim_distance_end" type="QString" value="0"/>
            <Option name="trim_distance_end_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="trim_distance_end_unit" type="QString" value="MM"/>
            <Option name="trim_distance_start" type="QString" value="0"/>
            <Option name="trim_distance_start_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="trim_distance_start_unit" type="QString" value="MM"/>
            <Option name="tweak_dash_pattern_on_corners" type="QString" value="0"/>
            <Option name="use_custom_dash" type="QString" value="0"/>
            <Option name="width_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
          </Option>
          <data_defined_properties>
            <Option type="Map">
              <Option name="name" type="QString" value=""/>
              <Option name="properties"/>
              <Option name="type" type="QString" value="collection"/>
            </Option>
          </data_defined_properties>
        </layer>
        <!-- High saturation line -->
        <layer class="SimpleLine" enabled="1" id="{uid()}" locked="0" pass="1">
          <Option type="Map">
            <Option name="align_dash_pattern" type="QString" value="0"/>
            <Option name="capstyle" type="QString" value="round"/>
            <Option name="customdash" type="QString" value="5;2"/>
            <Option name="customdash_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="customdash_unit" type="QString" value="MM"/>
            <Option name="draw_inside_polygon" type="QString" value="0"/>
            <Option name="joinstyle" type="QString" value="round"/>
            <Option name="line_color" type="QString" value="{color_val}"/>
            <Option name="line_style" type="QString" value="solid"/>
            <Option name="line_width" type="QString" value="{line_width}"/>
            <Option name="line_width_unit" type="QString" value="MM"/>
            <Option name="offset" type="QString" value="0"/>
            <Option name="offset_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="offset_unit" type="QString" value="MM"/>
            <Option name="ring_filter" type="QString" value="0"/>
            <Option name="trim_distance_end" type="QString" value="0"/>
            <Option name="trim_distance_end_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="trim_distance_end_unit" type="QString" value="MM"/>
            <Option name="trim_distance_start" type="QString" value="0"/>
            <Option name="trim_distance_start_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="trim_distance_start_unit" type="QString" value="MM"/>
            <Option name="tweak_dash_pattern_on_corners" type="QString" value="0"/>
            <Option name="use_custom_dash" type="QString" value="0"/>
            <Option name="width_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
          </Option>
          <data_defined_properties>
            <Option type="Map">
              <Option name="name" type="QString" value=""/>
              <Option name="properties"/>
              <Option name="type" type="QString" value="collection"/>
            </Option>
          </data_defined_properties>
        </layer>
      </symbol>""")

    cat_str = "\n".join(categories_xml)
    sym_str = "\n".join(symbols_xml)
    return f"""<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis layerType="Vector" styleCategories="Symbology|Temporal" version="4.2.1-Belém do Pará">
  <temporal accumulate="1" durationField="MMSI" durationUnit="min" enabled="1" endExpression="segment_end_time" endField="segment_end_time" fixedDuration="0" limitMode="0" mode="4" startExpression="&quot;segment_end_time&quot; - make_interval(seconds:= &#xa;&#x9;min(&#xa;&#x9;&#x9;second(make_interval( minutes:=15)), &#xa;&#x9;&#x9;second(&quot;segment_end_time&quot; - &quot;segment_start_time&quot;)&#xa;&#x9;)&#xa;)" startField="segment_start_time">
    <fixedRange>
      <start></start>
      <end></end>
    </fixedRange>
  </temporal>
  <renderer-v2 attr="VesselGroup" enableorderby="0" forceraster="0" referencescale="-1" symbollevels="1" type="categorizedSymbol">
    <categories>
{cat_str}
    </categories>
    <symbols>
{sym_str}
    </symbols>
    <rotation/>
    <sizescale/>
    <data-defined-properties>
      <Option type="Map">
        <Option name="name" type="QString" value=""/>
        <Option name="properties"/>
        <Option name="type" type="QString" value="collection"/>
      </Option>
    </data-defined-properties>
  </renderer-v2>
  <selection mode="Default">
    <selectionColor invalid="1"/>
  </selection>
  <blendMode>0</blendMode>
  <featureBlendMode>0</featureBlendMode>
  <layerGeometryType>1</layerGeometryType>
</qgis>
"""

# ---------------------------------------------------------------------------
# 5. fairway_centerline.qml
# Nautical dashed navigation fairway track with subtle white halo casing.
# ---------------------------------------------------------------------------
FAIRWAY_CENTERLINE_QML = f"""<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis layerType="Vector" styleCategories="Symbology" version="4.2.1-Belém do Pará">
  <renderer-v2 enableorderby="0" forceraster="0" referencescale="-1" symbollevels="1" type="singleSymbol">
    <symbols>
      <symbol alpha="1" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0" name="0" type="line">
        <data_defined_properties>
          <Option type="Map">
            <Option name="name" type="QString" value=""/>
            <Option name="properties"/>
            <Option name="type" type="QString" value="collection"/>
          </Option>
        </data_defined_properties>
        <!-- Subtle white halo casing -->
        <layer class="SimpleLine" enabled="1" id="{uid()}" locked="0" pass="0">
          <Option type="Map">
            <Option name="align_dash_pattern" type="QString" value="0"/>
            <Option name="capstyle" type="QString" value="flat"/>
            <Option name="customdash" type="QString" value="6;2.5"/>
            <Option name="customdash_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="customdash_unit" type="QString" value="MM"/>
            <Option name="draw_inside_polygon" type="QString" value="0"/>
            <Option name="joinstyle" type="QString" value="round"/>
            <Option name="line_color" type="QString" value="255,255,255,220,rgb:1,1,1,0.8627451"/>
            <Option name="line_style" type="QString" value="solid"/>
            <Option name="line_width" type="QString" value="1.0"/>
            <Option name="line_width_unit" type="QString" value="MM"/>
            <Option name="offset" type="QString" value="0"/>
            <Option name="offset_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="offset_unit" type="QString" value="MM"/>
            <Option name="ring_filter" type="QString" value="0"/>
            <Option name="trim_distance_end" type="QString" value="0"/>
            <Option name="trim_distance_end_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="trim_distance_end_unit" type="QString" value="MM"/>
            <Option name="trim_distance_start" type="QString" value="0"/>
            <Option name="trim_distance_start_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="trim_distance_start_unit" type="QString" value="MM"/>
            <Option name="tweak_dash_pattern_on_corners" type="QString" value="0"/>
            <Option name="use_custom_dash" type="QString" value="1"/>
            <Option name="width_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
          </Option>
          <data_defined_properties>
            <Option type="Map">
              <Option name="name" type="QString" value=""/>
              <Option name="properties"/>
              <Option name="type" type="QString" value="collection"/>
            </Option>
          </data_defined_properties>
        </layer>
        <!-- Deep nautical teal dashed fairway line -->
        <layer class="SimpleLine" enabled="1" id="{uid()}" locked="0" pass="1">
          <Option type="Map">
            <Option name="align_dash_pattern" type="QString" value="0"/>
            <Option name="capstyle" type="QString" value="flat"/>
            <Option name="customdash" type="QString" value="6;2.5"/>
            <Option name="customdash_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="customdash_unit" type="QString" value="MM"/>
            <Option name="draw_inside_polygon" type="QString" value="0"/>
            <Option name="joinstyle" type="QString" value="round"/>
            <Option name="line_color" type="QString" value="0,150,136,255,hsv:0.483,1.0,0.588,1"/>
            <Option name="line_style" type="QString" value="solid"/>
            <Option name="line_width" type="QString" value="0.55"/>
            <Option name="line_width_unit" type="QString" value="MM"/>
            <Option name="offset" type="QString" value="0"/>
            <Option name="offset_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="offset_unit" type="QString" value="MM"/>
            <Option name="ring_filter" type="QString" value="0"/>
            <Option name="trim_distance_end" type="QString" value="0"/>
            <Option name="trim_distance_end_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="trim_distance_end_unit" type="QString" value="MM"/>
            <Option name="trim_distance_start" type="QString" value="0"/>
            <Option name="trim_distance_start_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="trim_distance_start_unit" type="QString" value="MM"/>
            <Option name="tweak_dash_pattern_on_corners" type="QString" value="0"/>
            <Option name="use_custom_dash" type="QString" value="1"/>
            <Option name="width_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
          </Option>
          <data_defined_properties>
            <Option type="Map">
              <Option name="name" type="QString" value=""/>
              <Option name="properties"/>
              <Option name="type" type="QString" value="collection"/>
            </Option>
          </data_defined_properties>
        </layer>
      </symbol>
    </symbols>
    <rotation/>
    <sizescale/>
    <data-defined-properties>
      <Option type="Map">
        <Option name="name" type="QString" value=""/>
        <Option name="properties"/>
        <Option name="type" type="QString" value="collection"/>
      </Option>
    </data-defined-properties>
  </renderer-v2>
  <selection mode="Default">
    <selectionColor invalid="1"/>
  </selection>
  <blendMode>0</blendMode>
  <featureBlendMode>0</featureBlendMode>
  <layerGeometryType>1</layerGeometryType>
</qgis>
"""

# ---------------------------------------------------------------------------
# 6. fairway_mile_markers.qml
# Point marker for River Mile markers with crisp label showing River Mile.
# ---------------------------------------------------------------------------
FAIRWAY_MILE_MARKERS_QML = f"""<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis layerType="Vector" styleCategories="Symbology|Labeling" version="4.2.1-Belém do Pará">
  <renderer-v2 enableorderby="0" forceraster="0" referencescale="-1" symbollevels="0" type="singleSymbol">
    <symbols>
      <symbol alpha="1" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0" name="0" type="marker">
        <data_defined_properties>
          <Option type="Map">
            <Option name="name" type="QString" value=""/>
            <Option name="properties"/>
            <Option name="type" type="QString" value="collection"/>
          </Option>
        </data_defined_properties>
        <layer class="SimpleMarker" enabled="1" id="{uid()}" locked="0" pass="0">
          <Option type="Map">
            <Option name="angle" type="QString" value="0"/>
            <Option name="cap_style" type="QString" value="square"/>
            <Option name="color" type="QString" value="0,150,136,255,hsv:0.483,1.0,0.588,1"/>
            <Option name="horizontal_anchor_point" type="QString" value="1"/>
            <Option name="joinstyle" type="QString" value="bevel"/>
            <Option name="name" type="QString" value="circle"/>
            <Option name="offset" type="QString" value="0,0"/>
            <Option name="offset_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="offset_unit" type="QString" value="MM"/>
            <Option name="outline_color" type="QString" value="255,255,255,240,rgb:1,1,1,0.94117647"/>
            <Option name="outline_style" type="QString" value="solid"/>
            <Option name="outline_width" type="QString" value="0.4"/>
            <Option name="outline_width_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="outline_width_unit" type="QString" value="MM"/>
            <Option name="scale_method" type="QString" value="diameter"/>
            <Option name="size" type="QString" value="2.2"/>
            <Option name="size_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="size_unit" type="QString" value="MM"/>
            <Option name="vertical_anchor_point" type="QString" value="1"/>
          </Option>
          <data_defined_properties>
            <Option type="Map">
              <Option name="name" type="QString" value=""/>
              <Option name="properties"/>
              <Option name="type" type="QString" value="collection"/>
            </Option>
          </data_defined_properties>
        </layer>
      </symbol>
    </symbols>
    <rotation/>
    <sizescale/>
    <data-defined-properties>
      <Option type="Map">
        <Option name="name" type="QString" value=""/>
        <Option name="properties"/>
        <Option name="type" type="QString" value="collection"/>
      </Option>
    </data-defined-properties>
  </renderer-v2>
  <labeling type="simple">
    <settings calloutType="simple">
      <text-style blendMode="0" fieldName="concat('RM ', to_string(&quot;river_mile&quot;))" fontFamily="Sans-Serif" fontSize="7" fontSizeUnit="Point" fontStyle="Bold" fontWeight="75" isExpression="1" textColor="255,255,255,255">
        <text-buffer bufferColor="15,23,42,220" bufferDraw="1" bufferJoinStyle="128" bufferSize="0.8" bufferSizeUnits="MM"/>
      </text-style>
      <placement dist="1.2" distUnits="MM" maxCurvedCharAngleIn="25" maxCurvedCharAngleOut="-25" placement="1" priority="5" quadOffset="2" repeatDistance="0" repeatDistanceUnits="MM"/>
      <rendering displayAll="0" fontMinPixelSize="3" obstacle="1" scaleMax="0" scaleMin="250000" scaleVisibility="1"/>
    </settings>
  </labeling>
  <selection mode="Default">
    <selectionColor invalid="1"/>
  </selection>
  <blendMode>0</blendMode>
  <featureBlendMode>0</featureBlendMode>
  <layerGeometryType>0</layerGeometryType>
</qgis>
"""

def main():
    os.makedirs(STYLES_DIR, exist_ok=True)

    styles = {
        "trajectorized_points.qml": TRAJECTORIZED_POINTS_QML,
        "encounters.qml": make_encounters_qml(),
        "timeseries.qml": make_timeseries_qml(),
        "trajectories.qml": make_vesselgroup_line_qml("trajectories", "TrackStartTime", "TrackEndTime", line_width=0.45, casing_width=0.85),
        "segments.qml": make_segments_qml(line_width=0.40, casing_width=0.80),
        "stationary.qml": make_vesselgroup_line_qml("stationary", "segment_start_time", "segment_end_time", line_width=0.55, casing_width=0.95),
        "fairway_centerline.qml": FAIRWAY_CENTERLINE_QML,
        "fairway_mile_markers.qml": FAIRWAY_MILE_MARKERS_QML,
    }

    print(f"Writing styles to {STYLES_DIR}:")
    for fname, content in styles.items():
        fpath = os.path.join(STYLES_DIR, fname)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content.strip() + "\n")
        print(f"  -> {fname} ({len(content)} bytes)")

    # Also copy alongside geoparquet datasets in DATA_DIR for automatic QGIS loading
    if os.path.exists(DATA_DIR):
        print(f"\nCopying styles to {DATA_DIR} with matching basenames:")
        dataset_style_mapping = {
            "mississippi_1h_trajectorized_points.qml": "trajectorized_points.qml",
            "mississippi_1h_encounters.qml": "encounters.qml",
            "mississippi_1h_timeseries.qml": "timeseries.qml",
            "mississippi_1h_trajectories.qml": "trajectories.qml",
            "mississippi_1h_segments.qml": "segments.qml",
            "mississippi_1h_stationary.qml": "stationary.qml",
            "mississippi_fairway_centerline.qml": "fairway_centerline.qml",
            "mississippi_fairway_mile_markers.qml": "fairway_mile_markers.qml",
        }
        for target_name, src_name in dataset_style_mapping.items():
            src_path = os.path.join(STYLES_DIR, src_name)
            dst_path = os.path.join(DATA_DIR, target_name)
            shutil.copyfile(src_path, dst_path)
            print(f"  -> {target_name}")

if __name__ == "__main__":
    main()
