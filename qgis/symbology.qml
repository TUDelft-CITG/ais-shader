<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis styleCategories="Symbology" version="3.44.7-Solothurn">
 <renderer-v2 enableorderby="0" type="singleSymbol" referencescale="-1" forceraster="0" symbollevels="0">
  <symbols>
   <symbol is_animated="0" clip_to_extent="1" type="line" force_rhr="0" frame_rate="10" name="0" alpha="1">
    <data_defined_properties>
     <Option type="Map">
      <Option value="" type="QString" name="name"/>
      <Option name="properties"/>
      <Option value="collection" type="QString" name="type"/>
     </Option>
    </data_defined_properties>
    <!-- Geometry Generator Layer -->
    <layer locked="0" id="{geometry-generator-layer}" pass="0" class="GeometryGenerator" enabled="1">
     <Option type="Map">
      <Option value="Line" type="QString" name="SymbolType"/>
      <Option value="make_line(centroid($geometry), project(centroid($geometry), &quot;Frequency&quot; * 0.1, radians(main_angle($geometry) + if(&quot;Direction&quot; = 'up', 90, -90))))" type="QString" name="geometryModifier"/>
      <Option value="MM" type="QString" name="outputUnit"/>
      <Option value="3x:0,0,0,0,0,0" type="QString" name="outputUnitScale"/>
     </Option>
     <data_defined_properties>
      <Option type="Map">
       <Option value="" type="QString" name="name"/>
       <Option name="properties"/>
       <Option value="collection" type="QString" name="type"/>
      </Option>
     </data_defined_properties>
     <!-- Sub-symbol for rendering the generated line -->
     <symbol is_animated="0" clip_to_extent="1" type="line" force_rhr="0" frame_rate="10" name="@0@0" alpha="1">
      <data_defined_properties>
       <Option type="Map">
        <Option value="" type="QString" name="name"/>
        <Option name="properties"/>
        <Option value="collection" type="QString" name="type"/>
       </Option>
      </data_defined_properties>
      <layer locked="0" id="{sub-simple-line}" pass="0" class="SimpleLine" enabled="1">
       <Option type="Map">
        <Option value="0" type="QString" name="align_dash_pattern"/>
        <Option value="square" type="QString" name="capstyle"/>
        <Option value="5;2" type="QString" name="customdash"/>
        <Option value="3x:0,0,0,0,0,0" type="QString" name="customdash_map_unit_scale"/>
        <Option value="MM" type="QString" name="customdash_unit"/>
        <Option value="0" type="QString" name="dash_pattern_offset"/>
        <Option value="3x:0,0,0,0,0,0" type="QString" name="dash_pattern_offset_map_unit_scale"/>
        <Option value="MM" type="QString" name="dash_pattern_offset_unit"/>
        <Option value="0" type="QString" name="draw_inside_polygon"/>
        <Option value="bevel" type="QString" name="joinstyle"/>
        <Option value="255,99,71,255,rgb:1.0,0.388,0.278,1" type="QString" name="line_color"/>
        <Option value="solid" type="QString" name="line_style"/>
        <Option value="1.0" type="QString" name="line_width"/>
        <Option value="MM" type="QString" name="line_width_unit"/>
        <Option value="0" type="QString" name="offset"/>
        <Option value="3x:0,0,0,0,0,0" type="QString" name="offset_map_unit_scale"/>
        <Option value="MM" type="QString" name="offset_unit"/>
        <Option value="0" type="QString" name="ring_filter"/>
        <Option value="0" type="QString" name="trim_distance_end"/>
        <Option value="3x:0,0,0,0,0,0" type="QString" name="trim_distance_end_map_unit_scale"/>
        <Option value="MM" type="QString" name="trim_distance_end_unit"/>
        <Option value="0" type="QString" name="trim_distance_start"/>
        <Option value="3x:0,0,0,0,0,0" type="QString" name="trim_distance_start_map_unit_scale"/>
        <Option value="MM" type="QString" name="trim_distance_start_unit"/>
        <Option value="0" type="QString" name="tweak_dash_pattern_on_corners"/>
        <Option value="0" type="QString" name="use_custom_dash"/>
        <Option value="3x:0,0,0,0,0,0" type="QString" name="width_map_unit_scale"/>
       </Option>
       <data_defined_properties>
        <Option type="Map">
         <Option value="" type="QString" name="name"/>
         <Option name="properties"/>
         <Option value="collection" type="QString" name="type"/>
        </Option>
       </data_defined_properties>
      </layer>
     </symbol>
    </layer>
   </symbol>
  </symbols>
  <rotation/>
  <sizescale/>
  <data-defined-properties>
   <Option type="Map">
    <Option value="" type="QString" name="name"/>
    <Option name="properties"/>
    <Option value="collection" type="QString" name="type"/>
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
