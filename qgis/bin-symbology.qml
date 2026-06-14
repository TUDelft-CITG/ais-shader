<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis styleCategories="Symbology|Labeling" version="3.44.7-Solothurn" labelsEnabled="0">
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
        <layer locked="0" id="{d3799597-d9a2-4054-bd3a-517d9faa2dde}" pass="0" class="GeometryGenerator" enabled="1">
          <Option type="Map">
            <Option value="Line" type="QString" name="SymbolType"/>
            <Option value="  make_line(&#xa;      centroid($geometry),&#xa;      project(&#xa;        centroid($geometry),&#xa;        &quot;Frequency&quot; * 0.1,  -- The height/length of the bar. Adjust 0.1 to scale.&#xa;        radians(&#xa;          main_angle($geometry) + if(&quot;Direction&quot; = 'up', 90, -90)&#xa;        )&#xa;      )&#xa;    )" type="QString" name="geometryModifier"/>
            <Option value="MapUnit" type="QString" name="units"/>
          </Option>
          <data_defined_properties>
            <Option type="Map">
              <Option value="" type="QString" name="name"/>
              <Option name="properties"/>
              <Option value="collection" type="QString" name="type"/>
            </Option>
          </data_defined_properties>
          <symbol is_animated="0" clip_to_extent="1" type="line" force_rhr="0" frame_rate="10" name="@0@0" alpha="1">
            <data_defined_properties>
              <Option type="Map">
                <Option value="" type="QString" name="name"/>
                <Option name="properties"/>
                <Option value="collection" type="QString" name="type"/>
              </Option>
            </data_defined_properties>
            <layer locked="0" id="{825b66ad-0510-462c-acc3-7638991535a1}" pass="0" class="SimpleLine" enabled="1">
              <Option type="Map">
                <Option value="0" type="QString" name="align_dash_pattern"/>
                <Option value="square" type="QString" name="capstyle"/>
                <Option value="5;2" type="QString" name="customdash"/>
                <Option value="3x:0,0,0,0,0,0" type="QString" name="customdash_map_unit_scale"/>
                <Option value="RenderMetersInMapUnits" type="QString" name="customdash_unit"/>
                <Option value="0" type="QString" name="dash_pattern_offset"/>
                <Option value="3x:0,0,0,0,0,0" type="QString" name="dash_pattern_offset_map_unit_scale"/>
                <Option value="RenderMetersInMapUnits" type="QString" name="dash_pattern_offset_unit"/>
                <Option value="0" type="QString" name="draw_inside_polygon"/>
                <Option value="bevel" type="QString" name="joinstyle"/>
                <Option value="35,35,35,255,rgb:0.1372549,0.1372549,0.1372549,1" type="QString" name="line_color"/>
                <Option value="solid" type="QString" name="line_style"/>
                <Option value="0.26" type="QString" name="line_width"/>
                <Option value="RenderMetersInMapUnits" type="QString" name="line_width_unit"/>
                <Option value="0" type="QString" name="offset"/>
                <Option value="3x:0,0,0,0,0,0" type="QString" name="offset_map_unit_scale"/>
                <Option value="RenderMetersInMapUnits" type="QString" name="offset_unit"/>
                <Option value="0" type="QString" name="ring_filter"/>
                <Option value="0" type="QString" name="trim_distance_end"/>
                <Option value="3x:0,0,0,0,0,0" type="QString" name="trim_distance_end_map_unit_scale"/>
                <Option value="RenderMetersInMapUnits" type="QString" name="trim_distance_end_unit"/>
                <Option value="0" type="QString" name="trim_distance_start"/>
                <Option value="3x:0,0,0,0,0,0" type="QString" name="trim_distance_start_map_unit_scale"/>
                <Option value="RenderMetersInMapUnits" type="QString" name="trim_distance_start_unit"/>
                <Option value="0" type="QString" name="tweak_dash_pattern_on_corners"/>
                <Option value="0" type="QString" name="use_custom_dash"/>
                <Option value="3x:0,0,0,0,0,0" type="QString" name="width_map_unit_scale"/>
              </Option>
              <data_defined_properties>
                <Option type="Map">
                  <Option value="" type="QString" name="name"/>
                  <Option type="Map" name="properties">
                    <Option type="Map" name="outlineColor">
                      <Option value="true" type="bool" name="active"/>
                      <Option value="MedianSpeed" type="QString" name="field"/>
                      <Option type="Map" name="transformer">
                        <Option type="Map" name="d">
                          <Option type="Map" name="colorramp">
                            <Option value="[source]" type="QString" name="name"/>
                            <Option type="Map" name="properties">
                              <Option value="0,0,4,255,rgb:0,0,0.0156863,1" type="QString" name="color1"/>
                              <Option value="252,253,191,255,rgb:0.9882353,0.9921569,0.7490196,1" type="QString" name="color2"/>
                              <Option value="ccw" type="QString" name="direction"/>
                              <Option value="0" type="QString" name="discrete"/>
                              <Option value="gradient" type="QString" name="rampType"/>
                              <Option value="rgb" type="QString" name="spec"/>
                              <Option value="0.0196078;2,2,11,255,rgb:0.0078431,0.0078431,0.0431373,1;rgb;ccw:0.0392157;5,4,22,255,rgb:0.0196078,0.0156863,0.0862745,1;rgb;ccw:0.0588235;9,7,32,255,rgb:0.0352941,0.027451,0.1254902,1;rgb;ccw:0.0784314;14,11,43,255,rgb:0.054902,0.0431373,0.1686275,1;rgb;ccw:0.0980392;20,14,54,255,rgb:0.0784314,0.054902,0.2117647,1;rgb;ccw:0.117647;26,16,66,255,rgb:0.1019608,0.0627451,0.2588235,1;rgb;ccw:0.137255;33,17,78,255,rgb:0.1294118,0.0666667,0.3058824,1;rgb;ccw:0.156863;41,17,90,255,rgb:0.1607843,0.0666667,0.3529412,1;rgb;ccw:0.176471;49,17,101,255,rgb:0.1921569,0.0666667,0.3960784,1;rgb;ccw:0.196078;57,15,110,255,rgb:0.2235294,0.0588235,0.4313725,1;rgb;ccw:0.215686;66,15,117,255,rgb:0.2588235,0.0588235,0.4588235,1;rgb;ccw:0.235294;74,16,121,255,rgb:0.2901961,0.0627451,0.4745098,1;rgb;ccw:0.254902;82,19,124,255,rgb:0.3215686,0.0745098,0.4862745,1;rgb;ccw:0.27451;90,22,126,255,rgb:0.3529412,0.0862745,0.4941176,1;rgb;ccw:0.294118;98,25,128,255,rgb:0.3843137,0.0980392,0.5019608,1;rgb;ccw:0.313725;106,28,129,255,rgb:0.4156863,0.1098039,0.5058824,1;rgb;ccw:0.333333;114,31,129,255,rgb:0.4470588,0.1215686,0.5058824,1;rgb;ccw:0.352941;121,34,130,255,rgb:0.4745098,0.1333333,0.5098039,1;rgb;ccw:0.372549;129,37,129,255,rgb:0.5058824,0.145098,0.5058824,1;rgb;ccw:0.392157;137,40,129,255,rgb:0.5372549,0.1568627,0.5058824,1;rgb;ccw:0.411765;145,43,129,255,rgb:0.5686275,0.1686275,0.5058824,1;rgb;ccw:0.431373;153,45,128,255,rgb:0.6,0.1764706,0.5019608,1;rgb;ccw:0.45098;161,48,126,255,rgb:0.6313725,0.1882353,0.4941176,1;rgb;ccw:0.470588;170,51,125,255,rgb:0.6666667,0.2,0.4901961,1;rgb;ccw:0.490196;178,53,123,255,rgb:0.6980392,0.2078431,0.4823529,1;rgb;ccw:0.509804;186,56,120,255,rgb:0.7294118,0.2196078,0.4705882,1;rgb;ccw:0.529412;194,59,117,255,rgb:0.7607843,0.2313725,0.4588235,1;rgb;ccw:0.54902;202,62,114,255,rgb:0.7921569,0.2431373,0.4470588,1;rgb;ccw:0.568627;210,66,111,255,rgb:0.8235294,0.2588235,0.4352941,1;rgb;ccw:0.588235;217,70,107,255,rgb:0.8509804,0.2745098,0.4196078,1;rgb;ccw:0.607843;224,76,103,255,rgb:0.8784314,0.2980392,0.4039216,1;rgb;ccw:0.627451;231,82,99,255,rgb:0.9058824,0.3215686,0.3882353,1;rgb;ccw:0.647059;236,88,96,255,rgb:0.9254902,0.345098,0.3764706,1;rgb;ccw:0.666667;241,96,93,255,rgb:0.945098,0.3764706,0.3647059,1;rgb;ccw:0.686275;244,105,92,255,rgb:0.9568627,0.4117647,0.3607843,1;rgb;ccw:0.705882;247,114,92,255,rgb:0.9686275,0.4470588,0.3607843,1;rgb;ccw:0.72549;249,123,93,255,rgb:0.9764706,0.4823529,0.3647059,1;rgb;ccw:0.745098;251,133,96,255,rgb:0.9843137,0.5215686,0.3764706,1;rgb;ccw:0.764706;252,142,100,255,rgb:0.9882353,0.5568627,0.3921569,1;rgb;ccw:0.784314;253,152,105,255,rgb:0.9921569,0.5960784,0.4117647,1;rgb;ccw:0.803922;254,161,110,255,rgb:0.9960784,0.6313725,0.4313725,1;rgb;ccw:0.823529;254,170,116,255,rgb:0.9960784,0.6666667,0.454902,1;rgb;ccw:0.843137;254,180,123,255,rgb:0.9960784,0.7058824,0.4823529,1;rgb;ccw:0.862745;254,189,130,255,rgb:0.9960784,0.7411765,0.5098039,1;rgb;ccw:0.882353;254,198,138,255,rgb:0.9960784,0.7764706,0.5411765,1;rgb;ccw:0.901961;254,207,146,255,rgb:0.9960784,0.8117647,0.572549,1;rgb;ccw:0.921569;254,216,154,255,rgb:0.9960784,0.8470588,0.6039216,1;rgb;ccw:0.941176;253,226,163,255,rgb:0.9921569,0.8862745,0.6392157,1;rgb;ccw:0.960784;253,235,172,255,rgb:0.9921569,0.9215686,0.6745098,1;rgb;ccw:0.980392;252,244,182,255,rgb:0.9882353,0.9568627,0.7137255,1;rgb;ccw" type="QString" name="stops"/>
                            </Option>
                            <Option value="gradient" type="QString" name="type"/>
                          </Option>
                          <Option value="30" type="double" name="maxValue"/>
                          <Option value="0" type="double" name="minValue"/>
                          <Option value="" type="QString" name="nullColor"/>
                          <Option value="Magma" type="QString" name="rampName"/>
                        </Option>
                        <Option value="2" type="int" name="t"/>
                      </Option>
                      <Option value="2" type="int" name="type"/>
                    </Option>
                    <Option type="Map" name="outlineWidth">
                      <Option value="true" type="bool" name="active"/>
                      <Option value="&quot;BinWidth&quot;" type="QString" name="expression"/>
                      <Option value="3" type="int" name="type"/>
                    </Option>
                  </Option>
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
    <selectionSymbol>
      <symbol is_animated="0" clip_to_extent="1" type="line" force_rhr="0" frame_rate="10" name="" alpha="1">
        <data_defined_properties>
          <Option type="Map">
            <Option value="" type="QString" name="name"/>
            <Option name="properties"/>
            <Option value="collection" type="QString" name="type"/>
          </Option>
        </data_defined_properties>
        <layer locked="0" id="{c33de5be-c1fd-41c8-9e6f-31f1c00b24e0}" pass="0" class="SimpleLine" enabled="1">
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
            <Option value="35,35,35,255,rgb:0.1372549,0.1372549,0.1372549,1" type="QString" name="line_color"/>
            <Option value="solid" type="QString" name="line_style"/>
            <Option value="0.26" type="QString" name="line_width"/>
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
    </selectionSymbol>
  </selection>
  <blendMode>0</blendMode>
  <featureBlendMode>0</featureBlendMode>
  <layerGeometryType>1</layerGeometryType>
</qgis>
