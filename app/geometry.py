"""2D typography and watertight layered extrusion, dimensions in mm."""
import io, re, tempfile
from pathlib import Path
import numpy as np
import trimesh
from shapely.geometry import Polygon, GeometryCollection, Point, box
from shapely.ops import unary_union
from shapely import constrained_delaunay_triangles, affinity
from matplotlib.textpath import TextPath
from matplotlib.font_manager import FontProperties
from PIL import Image
import cairosvg
import cv2

FONTS={'DejaVu Sans':'DejaVu Sans','DejaVu Sans Mono':'DejaVu Sans Mono','Liberation Sans':'Liberation Sans','Liberation Serif':'Liberation Serif'}

def polygons(g):
    if g.is_empty:return []
    if isinstance(g,Polygon):return [g] if g.area>1e-5 else []
    if hasattr(g,'geoms'):return [p for x in g.geoms for p in polygons(x)]
    return []

def from_rings(rings):
    result=GeometryCollection()
    for ring in rings:
        if len(ring)<3:continue
        p=Polygon(ring)
        if not p.is_valid:p=p.buffer(0)
        if p.area>1e-7:result=result.symmetric_difference(p)
    return result.buffer(0)

def text_shape(word,font,height,font_bytes=None):
    if not word.strip():raise ValueError('Name cannot be empty')
    if font_bytes:
        if len(font_bytes)>2000000:raise ValueError('Font exceeds 2 MB')
        from fontTools.ttLib import TTFont
        with tempfile.TemporaryDirectory() as folder:
            filepath=Path(folder)/'upload.ttf';filepath.write_bytes(font_bytes)
            with TTFont(str(filepath),lazy=False) as parsed:
                if not {'glyf','CFF ','CFF2'}.intersection(parsed.keys()):raise ValueError('Font has no usable outlines')
            path=TextPath((0,0),word,size=100,prop=FontProperties(fname=str(filepath)))
    else:
        path=TextPath((0,0),word,size=100,prop=FontProperties(family=FONTS.get(font,'DejaVu Sans'),weight='bold'))
    geom=from_rings(path.to_polygons(closed_only=True))
    if geom.is_empty:raise ValueError('Font produced no geometry')
    minx,miny,maxx,maxy=geom.bounds
    geom=affinity.translate(geom,-minx,-miny)
    return affinity.scale(geom,xfact=height/(maxy-miny),yfact=height/(maxy-miny),origin=(0,0))

def svg_shape(svg_bytes,height):
    if len(svg_bytes)>150000:raise ValueError('SVG exceeds 150 KB')
    text=svg_bytes.decode('utf-8','ignore')
    if re.search(r'<\s*(script|image|foreignObject|use|style|iframe|animate|set)\b|\bon\w+\s*=|\b(href|src)\s*=|url\s*\(|<!DOCTYPE|<!ENTITY|<\?xml-stylesheet',text,re.I):
        raise ValueError('SVG must contain only self-contained vector shapes')
    png=cairosvg.svg2png(bytestring=svg_bytes,output_width=600,output_height=600)
    mask=(np.array(Image.open(io.BytesIO(png)).convert('RGBA'))[:,:,3]>100).astype('uint8')*255
    contours,hierarchy=cv2.findContours(mask,cv2.RETR_TREE,cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:raise ValueError('SVG contains no visible artwork')
    rings=[]
    for cnt in contours:
        pts=cv2.approxPolyDP(cnt,0.7,True).reshape(-1,2)
        if len(pts)>=3:rings.append([(float(x),float(-y)) for x,y in pts])
    geom=from_rings(rings)
    if geom.is_empty:raise ValueError('SVG contains no usable shapes')
    minx,miny,maxx,maxy=geom.bounds
    geom=affinity.translate(geom,-minx,-miny)
    return affinity.scale(geom,xfact=height/(maxy-miny),yfact=height/(maxy-miny),origin=(0,0))

def extrude(geom,z,height):
    verts=[];faces=[];pointidx={}
    def vertex(x,y,zz):
        key=(round(x,7),round(y,7),round(zz,7))
        if key not in pointidx:pointidx[key]=len(verts);verts.append((x,y,zz))
        return pointidx[key]
    for poly in polygons(geom):
        for tri in constrained_delaunay_triangles(poly).geoms:
            if not poly.covers(tri.representative_point()):continue
            coords=list(tri.exterior.coords)[:3]
            a,b,c=coords
            if (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])<0:coords=[a,c,b]
            bot=[vertex(x,y,z) for x,y in coords];top=[vertex(x,y,z+height) for x,y in coords]
            faces.extend([tuple(reversed(bot)),tuple(top)])
        for ring in [poly.exterior,*poly.interiors]:
            coords=list(ring.coords)
            orientation=ring.is_ccw;interior=ring is not poly.exterior
            if orientation==interior:coords=coords[::-1]
            for a,b in zip(coords,coords[1:]):
                ia=vertex(a[0],a[1],z);ib=vertex(b[0],b[1],z)
                ja=vertex(a[0],a[1],z+height);jb=vertex(b[0],b[1],z+height)
                faces.extend([(ia,ib,jb),(ia,jb,ja)])
    if not faces:return None
    mesh=trimesh.Trimesh(vertices=np.array(verts),faces=np.array(faces),process=False)
    if mesh.volume<0:mesh.invert()
    return mesh

def generate(name,opts,svg_bytes=None,font_bytes=None):
    height=float(opts.get('height',17))
    base_h=float(opts.get('baseHeight',2.4));mid_h=float(opts.get('midHeight',0.8));top_h=float(opts.get('topHeight',0.8))
    base_margin=float(opts.get('baseMargin',3.5));mid_margin=float(opts.get('midMargin',1.7))
    hole_r=float(opts.get('holeDiameter',4))/2
    if not(10<=height<=40 and 0.4<=base_h<=8 and 0.4<=mid_h<=6 and 0.4<=top_h<=6 and 1<=base_margin<=10 and 0.4<=mid_margin<=base_margin and 2<=hole_r*2<=8):
        raise ValueError('A dimension is outside its allowed range')
    shape=text_shape(name,opts.get('font','DejaVu Sans'),height,font_bytes)
    if svg_bytes:
        icon=svg_shape(svg_bytes,height*float(opts.get('iconScale',0.85)))
        gap=float(opts.get('iconGap',1.5))
        icon=affinity.translate(icon,xoff=shape.bounds[2]+gap,yoff=(shape.bounds[3]-icon.bounds[3])/2)
        shape=unary_union([shape,icon])
    if shape.bounds[2]>220:raise ValueError('Result exceeds 220mm wide')
    mid=shape.buffer(mid_margin,join_style=1,resolution=8)
    if opts.get('baseShape','outline')=='rectangle':
        minx,miny,maxx,maxy=shape.bounds
        base=box(minx-base_margin,miny-base_margin,maxx+base_margin,maxy+base_margin).buffer(1).buffer(-1)
    else:base=shape.buffer(base_margin,join_style=1,resolution=10)
    if opts.get('hole',True):
        minx,miny,maxx,maxy=base.bounds
        pos=opts.get('holePosition','left')
        if pos=='top':
            x=(minx+maxx)/2;y=maxy+hole_r+0.8
        else:
            x=minx+base_margin*.9 if pos=='left' else maxx-base_margin*.9
            y=maxy+(hole_r+1.5)*.55
        outer=Point(x,y).buffer(hole_r+2,resolution=32)
        hole=Point(x,y).buffer(hole_r,resolution=32)
        base=base.union(outer).difference(hole)
    fit=opts.get('physicalMode','auto')
    if fit in ('width','height'):
        target=float(opts.get('physicalSize',65))
        if not 15<=target<=220:raise ValueError('Physical dimension must be 15–220 mm')
        dim=base.bounds[2]-base.bounds[0] if fit=='width' else base.bounds[3]-base.bounds[1]
        factor=target/dim
        base=affinity.scale(base,xfact=factor,yfact=factor,origin=(0,0))
        mid=affinity.scale(mid,xfact=factor,yfact=factor,origin=(0,0))
        shape=affinity.scale(shape,xfact=factor,yfact=factor,origin=(0,0))
    if base.bounds[2]-base.bounds[0]>240 or base.bounds[3]-base.bounds[1]>240:raise ValueError('Keychain exceeds 240mm')
    meshes=[]
    for label,poly,z,h in [('Base',base,0,base_h),('Border',mid,base_h,mid_h),('Letters',shape,base_h+mid_h,top_h)]:
        mesh=extrude(poly,z,h)
        if mesh is None:raise ValueError('Could not produce printable geometry')
        meshes.append((label,mesh))
    return meshes
