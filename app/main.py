import io,re,zipfile,json,os,hmac,base64
from fastapi import FastAPI,Form,UploadFile,File,HTTPException,Request
from fastapi.responses import Response,FileResponse,PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from .geometry import generate
from .exports import to_3mf,to_stl
import numpy as np

app=FastAPI(title="KeyForge v0.5")
@app.middleware("http")
async def require_password(request:Request,call_next):
    if request.url.path=='/api/health':return await call_next(request)
    password=os.getenv('KEYFORGE_PASSWORD','')
    if not password:
        if os.getenv('RENDER'):return PlainTextResponse('KEYFORGE_PASSWORD is not configured',status_code=503)
        return await call_next(request)
    username=os.getenv('KEYFORGE_USERNAME','keyforge')
    supplied=request.headers.get('authorization','');valid=False
    if supplied.startswith('Basic '):
        try:
            raw=base64.b64decode(supplied[6:],validate=True).decode('utf-8')
            u,p=raw.split(':',1)
            valid=hmac.compare_digest(u,username) and hmac.compare_digest(p,password)
        except (ValueError,UnicodeError):pass
    if not valid:return PlainTextResponse('KeyForge sign-in required',status_code=401,headers={'WWW-Authenticate':'Basic realm="KeyForge"'})
    return await call_next(request)

STATIC=Path(__file__).resolve().parent.parent/'static'
app.mount('/static',StaticFiles(directory=STATIC),name='static')
@app.get('/')
def home():return FileResponse(STATIC/'index.html')
@app.get('/api/health')
def health():return {'ok':True,'version':'0.5'}

def parse(names,settings,fmt):
    opts=json.loads(settings)
    if not isinstance(opts,dict):raise ValueError('Invalid settings')
    name_list=list(dict.fromkeys(x.strip() for x in names.splitlines() if x.strip()))
    if not name_list or len(name_list)>50:raise ValueError('Enter between 1 and 50 names')
    if any(len(x)>35 for x in name_list):raise ValueError('Names must have at most 35 characters')
    if fmt not in ('stl','3mf'):raise ValueError('Invalid format')
    return opts,name_list

async def uploads(svg,font):
    a=await svg.read() if svg else None;b=await font.read() if font else None
    if a and len(a)>150000:raise ValueError('SVG exceeds 150 KB')
    if b and len(b)>2000000:raise ValueError('Font exceeds 2 MB')
    return a,b

def combined_plate(models,spacing=8,bed_width=256,bed_height=256):
    placed=[];x=y=row_h=0
    for name,meshes in models:
        bounds=np.array([m.bounds for _,m in meshes])
        lo=bounds[:,0,:].min(axis=0);hi=bounds[:,1,:].max(axis=0)
        w,h=hi[0]-lo[0],hi[1]-lo[1]
        if w>bed_width or h>bed_height:raise ValueError(f'{name} exceeds print-bed dimensions')
        if x+w>bed_width and x>0:x=0;y+=row_h+spacing;row_h=0
        if y+h>bed_height:raise ValueError('Batch exceeds print-bed area')
        for layer,mesh in meshes:
            cp=mesh.copy();cp.apply_translation([x-lo[0]-bed_width/2,y-lo[1]-bed_height/2,0])
            placed.append((f'{name} — {layer}',cp))
        x+=w+spacing;row_h=max(row_h,h)
    return placed

@app.post('/api/export')
async def export(names:str=Form(...),settings:str=Form('{}'),format:str=Form('3mf'),plate:bool=Form(False),svg:UploadFile|None=File(None),font:UploadFile|None=File(None)):
    try:
        opts,items=parse(names,settings,format)
        svgdata,fontdata=await uploads(svg,font)
        models=[(n,generate(n,opts,svgdata,fontdata)) for n in items]
        if plate:
            bed=float(opts.get('bedSize',256));spacing=float(opts.get('plateSpacing',8))
            if not(100<=bed<=400 and 2<=spacing<=30):raise ValueError('Invalid plate settings')
            mesh=combined_plate(models,spacing,bed,bed)
            payload=to_3mf(mesh,opts.get('colors')) if format=='3mf' else to_stl(mesh)
            return Response(content=payload,media_type='application/octet-stream',headers={'Content-Disposition':f'attachment; filename="KeyForge_plate.{format}"'})
        results=[]
        for name,mesh in models:
            payload=to_3mf(mesh,opts.get('colors')) if format=='3mf' else to_stl(mesh)
            safe=re.sub(r'[^\w-]+','_',name,flags=re.UNICODE).strip('_')[:40] or 'keychain'
            results.append((safe,payload))
        if len(results)==1:return Response(content=results[0][1],media_type='application/octet-stream',headers={'Content-Disposition':f'attachment; filename="{results[0][0]}.{format}"'})
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for i,(name,payload) in enumerate(results):z.writestr(f'{i+1:02d}_{name}.{format}',payload)
        return Response(content=out.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="KeyForge_batch.zip"'})
    except Exception as e:raise HTTPException(status_code=400,detail=str(e))

@app.post('/api/preview')
async def preview(name:str=Form(...),settings:str=Form('{}'),svg:UploadFile|None=File(None),font:UploadFile|None=File(None)):
    try:
        opts,items=parse(name,settings,'3mf')
        if len(items)!=1:raise ValueError('Preview one name at a time')
        svgdata,fontdata=await uploads(svg,font)
        meshes=generate(items[0],opts,svgdata,fontdata)
        shapes=[];bounds=[]
        for layer,mesh in meshes:
            v=np.asarray(mesh.vertices);f=np.asarray(mesh.faces)
            if len(f)>70000:raise ValueError('Preview too complex')
            shapes.append({'layer':layer,'vertices':np.round(v,3).tolist(),'faces':f.tolist()})
            bounds.append(mesh.bounds)
        xyz=np.array(bounds);lo=xyz[:,0,:].min(axis=0);hi=xyz[:,1,:].max(axis=0)
        return {'meshes':shapes,'dimensions':{'width':round(float(hi[0]-lo[0]),2),'height':round(float(hi[1]-lo[1]),2),'depth':round(float(hi[2]-lo[2]),2)}}
    except Exception as e:raise HTTPException(status_code=400,detail=str(e))
