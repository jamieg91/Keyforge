"""Multi-object colour-assigned 3MF and STL exports."""
import io,zipfile,xml.etree.ElementTree as ET,re
import trimesh
CORE='http://schemas.microsoft.com/3dmanufacturing/core/2015/02'
ET.register_namespace('',CORE)
COLORS=['#193D32','#F3A58B','#FFF8E9']

def to_3mf(meshes,colors=None):
    colors=colors or COLORS
    root=ET.Element('{%s}model'%CORE,{'unit':'millimeter','xml:lang':'en-US'})
    resources=ET.SubElement(root,'{%s}resources'%CORE)
    mats=ET.SubElement(resources,'{%s}basematerials'%CORE,{'id':'1'})
    # Only include actually-used layers; two-colour exports contain two materials
    # and two physical objects, rather than a hidden or zero-height middle layer.
    all_layers=['Base','Border','Letters']
    used_layers=[layer for layer in all_layers if any(name.endswith(layer) for name,_ in meshes)]
    if not used_layers:raise ValueError('No layers to export')
    for name in used_layers:
        i=all_layers.index(name)
        color=colors[i] if isinstance(colors,list) and i<len(colors) else COLORS[i]
        if not isinstance(color,str) or not re.fullmatch('#[0-9a-fA-F]{6}',color):color=COLORS[i]
        ET.SubElement(mats,'{%s}base'%CORE,{'name':name,'displaycolor':color.upper()+'FF'})
    build=ET.SubElement(root,'{%s}build'%CORE)
    for i,(name,mesh) in enumerate(meshes):
        objid=i+2
        matched=next((layer for layer in used_layers if name.endswith(layer)),None)
        if matched is None:raise ValueError('Unexpected layer: '+name)
        layer=used_layers.index(matched)
        obj=ET.SubElement(resources,'{%s}object'%CORE,{'id':str(objid),'type':'model','name':name,'pid':'1','pindex':str(layer)})
        geo=ET.SubElement(obj,'{%s}mesh'%CORE)
        vertices=ET.SubElement(geo,'{%s}vertices'%CORE)
        for v in mesh.vertices:
            ET.SubElement(vertices,'{%s}vertex'%CORE,{'x':str(float(v[0])),'y':str(float(v[1])),'z':str(float(v[2]))})
        triangles=ET.SubElement(geo,'{%s}triangles'%CORE)
        for f in mesh.faces:ET.SubElement(triangles,'{%s}triangle'%CORE,{'v1':str(int(f[0])),'v2':str(int(f[1])),'v3':str(int(f[2]))})
        ET.SubElement(build,'{%s}item'%CORE,{'objectid':str(objid)})
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','''<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>''')
        z.writestr('_rels/.rels','''<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>''')
        z.writestr('3D/3dmodel.model',ET.tostring(root,encoding='utf-8',xml_declaration=True))
    return out.getvalue()

def to_stl(meshes):
    return trimesh.util.concatenate([m for _,m in meshes]).export(file_type='stl')
