"""2D typography and watertight layered extrusion, dimensions in mm."""
import io, re, tempfile
from pathlib import Path
import numpy as np
import trimesh
from shapely.geometry import Polygon, GeometryCollection, Point, LineString, box
from shapely.ops import unary_union, nearest_points
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

def get_number(opts,key,default,minimum,maximum,label):
    try:
        value=float(opts.get(key,default))
    except (TypeError,ValueError):
        raise ValueError(f'{key}: {label} must be a number')
    if not np.isfinite(value) or not minimum<=value<=maximum:
        raise ValueError(f'{key}: {label} must be between {minimum:g} and {maximum:g} mm')
    return value


def connected_bubble(raw, padding, smooth=1.8):
    """Join neighbouring letters without leaving enclosed background cutouts."""
    geom=raw.buffer(padding, join_style=1, resolution=12)
    geom=unary_union(polygons(geom))
    # Morphological closing bridges narrow seams and softens concave notches.
    geom=geom.buffer(smooth, join_style=1).buffer(-smooth, join_style=1)
    parts=polygons(geom)
    # If a font has unusually wide character spacing, add local, rounded
    # connections rather than allowing disconnected keychain islands.
    for _ in range(80):
        if len(parts)<=1:break
        parts=sorted(parts,key=lambda p:p.area,reverse=True)
        main=parts[0]
        other=min(parts[1:],key=lambda p:main.distance(p))
        a,b=nearest_points(main,other)
        bridge=LineString([a,b]).buffer(max(1.3,min(padding,3.0)*.52),cap_style=1)
        geom=unary_union([geom,bridge]).buffer(0)
        parts=polygons(geom)
    if len(parts)!=1:
        raise ValueError('baseMargin: Could not join the letter backing; increase outer padding')
    # Bubble backing must be solid, including the counters in O, P, A etc.
    solid=Polygon(parts[0].exterior)
    if not solid.is_valid:solid=solid.buffer(0)
    return solid


def reinforced_tab(base, raw, pos, hole_r, wall):
    """Half-circle above letter corner with a deep, rounded integral stem."""
    minx,miny,maxx,maxy=base.bounds
    outer_r=hole_r+wall
    left=pos in ('top_left','left')
    right=pos in ('top_right','right')
    if not (left or right or pos in ('top_center','top')):
        raise ValueError('holePosition: Choose a supported keyring position')
    if left or right:
        # Use the first/last glyph region, not the tallest glyph globally.
        width=max(outer_r*3.0,raw.bounds[3]-raw.bounds[1])
        a=(raw.bounds[0] if left else raw.bounds[2])
        clipping=box(a-1 if left else a-width,miny-5,a+width if left else a+1,maxy+5)
        near=base.intersection(clipping)
        local_top=near.bounds[3] if not near.is_empty else maxy
        x=minx+outer_r*0.95 if left else maxx-outer_r*0.95
    else:
        local_top=maxy
        x=(minx+maxx)*0.5
    # Centre sits just above the local roof so the lower semicircle is
    # absorbed into the backing instead of protruding on a weak stalk.
    y=local_top+outer_r*0.16
    cap=Point(x,y).buffer(outer_r,resolution=36)
    stem=LineString([(x,y-outer_r*0.2),(x,local_top-outer_r*1.6)]).buffer(
        max(outer_r*0.70,hole_r+wall*0.5),cap_style=1)
    joined=unary_union([base,cap,stem]).buffer(0)
    if len(polygons(joined))!=1:
        raise ValueError('tabPadding: Tab could not join to backing; increase its wall thickness')
    hole=Point(x,y).buffer(hole_r,resolution=36)
    # The hole must sit in a supported perimeter; do not accidentally cut
    # through text or leave a floating tab.
    result=joined.difference(hole).buffer(0)
    if len(polygons(result))!=1:
        raise ValueError('tabPadding: Tab disconnected after hole; increase wall thickness')
    return result


def generate(name,opts,svg_bytes=None,font_bytes=None):
    height=get_number(opts,'height',17,10,40,'Text height')
    base_h=get_number(opts,'baseHeight',2.4,0.4,8,'Base thickness')
    top_h=get_number(opts,'topHeight',0.8,0.4,6,'Raised text height')
    border_enabled=opts.get('borderEnabled',True) not in (False,0,'false','False','0')
    mid_h=get_number(opts,'midHeight',0.8,0.4,6,'Border height') if border_enabled else 0
    base_margin=get_number(opts,'baseMargin',4.8,1.5,12,'Outer backing padding')
    mid_margin=get_number(opts,'midMargin',2.3,0.4,12,'Inner border width') if border_enabled else 0
    weight=get_number(opts,'textBoldness',0.0,0,1.5,'Text boldness')
    diameter=get_number(opts,'holeDiameter',4,2.5,8,'Hole diameter')
    tab_padding=get_number(opts,'tabPadding',2.5,2,5,'Tab wall thickness')
    if border_enabled and mid_margin>base_margin-0.5:
        raise ValueError('midMargin: Inner border must be at least 0.5 mm smaller than outer padding')
    if weight>=base_margin-0.5:
        raise ValueError('textBoldness: Reduce text boldness or increase outer padding')

    raw=text_shape(name,opts.get('font','DejaVu Sans'),height,font_bytes)
    if svg_bytes:
        icon_height=height*get_number(opts,'iconScale',0.85,0.3,1.5,'Symbol scale')
        icon=svg_shape(svg_bytes,icon_height)
        gap=get_number(opts,'iconGap',1.5,0.5,10,'Artwork gap')
        icon=affinity.translate(icon,xoff=raw.bounds[2]+gap,
                                yoff=(raw.bounds[3]-icon.bounds[3])/2)
        raw=unary_union([raw,icon])
    if raw.bounds[2]-raw.bounds[0]>220:
        raise ValueError('name: Text is too long; choose a shorter name or smaller text height')

    # Construct bubble from original glyph contours, not the fattened text.
    if opts.get('baseShape','outline')=='rectangle':
        x0,y0,x1,y1=raw.bounds
        base=box(x0-base_margin,y0-base_margin,x1+base_margin,y1+base_margin)
        base=base.buffer(1.2).buffer(-1.2)
    else:
        base=connected_bubble(raw,base_margin)
    # Stroke expansion controls the visual balance of ink versus outline.
    letters=raw.buffer(weight,join_style=1,resolution=12) if weight else raw
    mid=connected_bubble(raw,mid_margin,smooth=min(mid_margin*0.5,1.0)) if border_enabled else None
    # A continuous backing is essential even when letter spacing is wide.
    if opts.get('hole',True):
        base=reinforced_tab(base,raw,opts.get('holePosition','top_left'),
                            diameter/2,tab_padding)
    fit=opts.get('physicalMode','auto')
    if fit in ('width','height'):
        target=get_number(opts,'physicalSize',65,15,220,'Overall target size')
        dim=base.bounds[2]-base.bounds[0] if fit=='width' else base.bounds[3]-base.bounds[1]
        factor=target/dim
        if factor<0.55:
            raise ValueError('physicalSize: Target size is too small to retain a strong keyring; increase it')
        base=affinity.scale(base,xfact=factor,yfact=factor,origin=(0,0))
        if border_enabled:mid=affinity.scale(mid,xfact=factor,yfact=factor,origin=(0,0))
        letters=affinity.scale(letters,xfact=factor,yfact=factor,origin=(0,0))
    elif fit!='auto':
        raise ValueError('physicalMode: Choose auto, width or height')
    if base.bounds[2]-base.bounds[0]>240 or base.bounds[3]-base.bounds[1]>240:
        raise ValueError('physicalSize: Keychain exceeds 240 mm')

    layers=[('Base',base,0,base_h)]
    if border_enabled:layers.append(('Border',mid,base_h,mid_h))
    layers.append(('Letters',letters,base_h+mid_h,top_h))
    meshes=[]
    for label,poly,z,h in layers:
        mesh=extrude(poly,z,h)
        if mesh is None:raise ValueError('baseMargin: Failed to create printable geometry')
        meshes.append((label,mesh))
    return meshes
