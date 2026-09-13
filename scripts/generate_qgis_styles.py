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
      <symbol alpha="0.438" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0" name="0" type="marker">
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
            <Option name="color" type="QString" value="37,100,235,240,hsv:0.61388889,0.84313725,0.92156863,0.94117647"/>
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
                  <Option name="expression" type="QString" value="coalesce(if(&quot;heading&quot; &lt; 360 AND &quot;heading&quot; > 0, &quot;heading&quot;, null), if(&quot;cog&quot; &lt; 360, &quot;cog&quot;, null), 0)"/>
                  <Option name="type" type="int" value="3"/>
                </Option>
                <Option name="name" type="Map">
                  <Option name="active" type="bool" value="true"/>
                  <Option name="expression" type="QString" value="if((&quot;heading&quot; is null or &quot;heading&quot; >= 360 or &quot;heading&quot; = 0) and (&quot;cog&quot; is null or &quot;cog&quot; >= 360 or &quot;cog&quot; = 0), 'circle', 'arrow')"/>
                  <Option name="type" type="int" value="3"/>
                </Option>
                <Option name="size" type="Map">
                  <Option name="active" type="bool" value="true"/>
                  <Option name="expression" type="QString" value="if((&quot;heading&quot; is null or &quot;heading&quot; >= 360 or &quot;heading&quot; = 0) and (&quot;cog&quot; is null or &quot;cog&quot; >= 360 or &quot;cog&quot; = 0), coalesce(&quot;beam&quot;, 12), coalesce(&quot;length&quot;, 25))"/>
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
              <Option name="properties" type="Map">
                <Option name="opacity" type="Map">
                  <Option name="active" type="bool" value="true"/>
                  <Option name="expression" type="QString" value="if(&quot;min_distance_m&quot; &lt;= 100, 1.0, 0.0)"/>
                  <Option name="type" type="int" value="3"/>
                </Option>
              </Option>
              <Option name="type" type="QString" value="collection"/>
            </Option>
          </data_defined_properties>
        </layer>
      </symbol>""")

    cat_str = "\n".join(categories_xml)
    sym_str = "\n".join(symbols_xml)
    return f"""<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis layerType="Vector" styleCategories="LayerConfiguration|Symbology|Temporal" version="4.2.1-Belém do Pará">
  <subsetString>&quot;min_distance_m&quot; &lt;= 100</subsetString>
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
        <!-- Single clean proximity line -->
        <layer class="SimpleLine" enabled="1" id="{uid()}" locked="0" pass="0">
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
              <Option name="properties" type="Map">
                <Option name="opacity" type="Map">
                  <Option name="active" type="bool" value="true"/>
                  <Option name="expression" type="QString" value="if(&quot;distance_m&quot; &lt;= 100, 1.0, 0.0)"/>
                  <Option name="type" type="int" value="3"/>
                </Option>
              </Option>
              <Option name="type" type="QString" value="collection"/>
            </Option>
          </data_defined_properties>
        </layer>
      </symbol>""")

    cat_str = "\n".join(categories_xml)
    sym_str = "\n".join(symbols_xml)
    return f"""<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis layerType="Vector" styleCategories="LayerConfiguration|Symbology|Temporal" version="4.2.1-Belém do Pará">
  <subsetString>&quot;distance_m&quot; &lt;= 100</subsetString>
  <temporal accumulate="0" durationField="distance_m" durationUnit="min" enabled="1" endExpression="" endField="" fixedDuration="30" limitMode="0" mode="1" startExpression="" startField="timestamp">
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
    ("commercial", "30,136,229,255,hsv:0.578,0.869,0.898,1"),      # Commercial (Azure)
    ("Other", "120,144,156,255,hsv:0.556,0.231,0.612,1"),         # Muted Blue-Grey
    ("NULL", "158,158,158,255,hsv:0.0,0.0,0.62,1"),
]

def make_vesselgroup_line_qml(layer_type_name, start_field, end_field, line_width=0.45):
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
        <!-- Single clean high saturation line -->
        <layer class="SimpleLine" enabled="1" id="{uid()}" locked="0" pass="0">
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
  <renderer-v2 attr="VesselGroup" enableorderby="0" forceraster="0" referencescale="-1" symbollevels="0" type="categorizedSymbol">
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
# LineString segments categorized by VesselGroup with solid opacity and clean width.
# ---------------------------------------------------------------------------
def make_segments_qml(line_width=0.65):
    return make_vesselgroup_line_qml("segments", "segment_start_time", "segment_end_time", line_width=line_width)

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
        <!-- Single clean deep nautical teal dashed fairway line -->
        <layer class="SimpleLine" enabled="1" id="{uid()}" locked="0" pass="0">
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

# ---------------------------------------------------------------------------
# 7. fairway_sections.qml
# Rijkswaterstaat FIS Layer 58 fairway links with labeling
# ---------------------------------------------------------------------------
FAIRWAY_SECTIONS_QML = f"""<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis layerType="Vector" styleCategories="Symbology|Labeling" version="4.2.1-Belém do Pará">
  <renderer-v2 enableorderby="0" forceraster="0" referencescale="-1" symbollevels="0" type="singleSymbol">
    <symbols>
      <symbol alpha="0.8" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0" name="0" type="line">
        <data_defined_properties>
          <Option type="Map">
            <Option name="name" type="QString" value=""/>
            <Option name="properties"/>
            <Option name="type" type="QString" value="collection"/>
          </Option>
        </data_defined_properties>
        <layer class="SimpleLine" enabled="1" id="{uid()}" locked="0" pass="0">
          <Option type="Map">
            <Option name="align_dash_pattern" type="QString" value="0"/>
            <Option name="capstyle" type="QString" value="round"/>
            <Option name="customdash" type="QString" value="5;2"/>
            <Option name="customdash_map_unit_scale" type="QString" value="3x:0,0,0,0,0,0"/>
            <Option name="customdash_unit" type="QString" value="MM"/>
            <Option name="draw_inside_polygon" type="QString" value="0"/>
            <Option name="joinstyle" type="QString" value="round"/>
            <Option name="line_color" type="QString" value="14,165,233,220,hsv:0.553,0.940,0.914,0.863"/>
            <Option name="line_style" type="QString" value="solid"/>
            <Option name="line_width" type="QString" value="0.5"/>
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
      <text-style blendMode="0" fieldName="concat(coalesce(&quot;fairway_name&quot;, &quot;name&quot;), ' (km ', to_string(round(&quot;routekmbegin&quot;, 1)), '-', to_string(round(&quot;routekmend&quot;, 1)), ')') " fontFamily="Sans-Serif" fontSize="7" fontSizeUnit="Point" fontWeight="50" isExpression="1" textColor="255,255,255,255">
        <text-buffer bufferColor="15,23,42,220" bufferDraw="1" bufferJoinStyle="128" bufferSize="0.8" bufferSizeUnits="MM"/>
      </text-style>
      <placement dist="1.0" distUnits="MM" maxCurvedCharAngleIn="25" maxCurvedCharAngleOut="-25" placement="2" priority="5" repeatDistance="150" repeatDistanceUnits="MM"/>
      <rendering displayAll="0" fontMinPixelSize="3" obstacle="1" scaleMax="0" scaleMin="100000" scaleVisibility="1"/>
    </settings>
  </labeling>
  <selection mode="Default">
    <selectionColor invalid="1"/>
  </selection>
  <blendMode>0</blendMode>
  <featureBlendMode>0</featureBlendMode>
  <layerGeometryType>1</layerGeometryType>
</qgis>
"""

import sqlite3

def embed_gpkg_layer_styles(gpkg_path: str, styles: dict):
    """Embed QML styles directly into the GeoPackage SQLite layer_styles table."""
    if not os.path.exists(gpkg_path):
        return
    print(f"\nEmbedding styles into GeoPackage: {gpkg_path}")
    conn = sqlite3.connect(gpkg_path)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS layer_styles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        f_table_catalog TEXT DEFAULT '',
        f_table_schema TEXT DEFAULT '',
        f_table_name TEXT NOT NULL,
        f_geometry_column TEXT,
        styleName TEXT,
        styleQML TEXT,
        styleSLD TEXT,
        useAsDefault BOOLEAN,
        description TEXT,
        owner TEXT,
        ui TEXT,
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cur.execute("SELECT table_name, column_name FROM gpkg_geometry_columns")
    geom_cols = dict(cur.fetchall())

    for layer_name, qml_content in styles.items():
        if layer_name not in geom_cols:
            continue
        geom_col = geom_cols[layer_name]
        cur.execute("DELETE FROM layer_styles WHERE f_table_name = ? AND styleName = 'default'", (layer_name,))
        cur.execute("""
        INSERT INTO layer_styles (
            f_table_catalog, f_table_schema, f_table_name, f_geometry_column,
            styleName, styleQML, styleSLD, useAsDefault, description, owner, ui
        ) VALUES ('', '', ?, ?, 'default', ?, '', 1, 'Default QGIS styling with temporal controller configuration', 'ais-shader', '')
        """, (layer_name, geom_col, qml_content.strip()))
        print(f"  -> Embedded default style for layer: {layer_name}")
    conn.commit()
    conn.close()


def main():
    os.makedirs(STYLES_DIR, exist_ok=True)

    points_qml = TRAJECTORIZED_POINTS_QML
    enc_qml = make_encounters_qml()
    ts_qml = make_timeseries_qml()
    traj_qml = make_vesselgroup_line_qml("trajectories", "TrackStartTime", "TrackEndTime", line_width=0.45)
    segs_qml = make_segments_qml()
    stat_qml = make_vesselgroup_line_qml("stationary", "segment_start_time", "segment_end_time", line_width=0.50)
    stat_vessels_qml = make_vesselgroup_line_qml("stationary_vessels", "segment_start_time", "segment_end_time", line_width=0.50)
    fairway_qml = FAIRWAY_CENTERLINE_QML
    sections_qml = FAIRWAY_SECTIONS_QML
    markers_qml = FAIRWAY_MILE_MARKERS_QML

    styles = {
        "trajectorized_points.qml": points_qml,
        "encounters.qml": enc_qml,
        "timeseries.qml": ts_qml,
        "trajectories.qml": traj_qml,
        "segments.qml": segs_qml,
        "stationary.qml": stat_qml,
        "stationary_vessels.qml": stat_vessels_qml,
        "fairway_centerline.qml": fairway_qml,
        "fairway_sections.qml": sections_qml,
        "fairway_mile_markers.qml": markers_qml,
    }

    print(f"Writing styles to {STYLES_DIR}:")
    for fname, content in styles.items():
        fpath = os.path.join(STYLES_DIR, fname)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content.strip() + "\n")
        print(f"  -> {fname} ({len(content)} bytes)")

    # 1. Copy styles to Mississippi directory
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

    # 2. Copy styles to EURIS directory and embed directly into GeoPackages
    euris_dir = "/scratch-shared/fbaart/data/euris_crawl"
    if os.path.exists(euris_dir):
        print(f"\nCopying styles to {euris_dir}:")
        euris_layer_styles = {
            "fairway_centerline": fairway_qml,
            "fairway_sections": sections_qml,
            "trajectorized_points": points_qml,
            "trajectories": traj_qml,
            "segments": segs_qml,
            "stationary_vessels": stat_vessels_qml,
            "encounters": enc_qml,
            "timeseries": ts_qml,
        }

        # Save generic sidecar QMLs in euris directory
        for layer_name, qml_content in euris_layer_styles.items():
            # Generic <layer>.qml
            p1 = os.path.join(euris_dir, f"{layer_name}.qml")
            with open(p1, "w", encoding="utf-8") as f:
                f.write(qml_content.strip() + "\n")
            # Prefixed euris_encounters_<layer>.qml
            p2 = os.path.join(euris_dir, f"euris_encounters_{layer_name}.qml")
            with open(p2, "w", encoding="utf-8") as f:
                f.write(qml_content.strip() + "\n")
            print(f"  -> {layer_name}.qml & euris_encounters_{layer_name}.qml")

        # Embed into all master euris GeoPackages
        gpkgs = [
            os.path.join(euris_dir, "euris_encounters.gpkg"),
            os.path.join(euris_dir, "euris_encounters_20260913_174753.gpkg"),
        ]
        for gpkg in gpkgs:
            if os.path.exists(gpkg):
                embed_gpkg_layer_styles(gpkg, euris_layer_styles)

    print("\nQGIS styles and temporal axes generation complete!")


if __name__ == "__main__":
    main()
