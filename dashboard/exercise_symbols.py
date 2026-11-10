"""Shared vector geometry for SVG and PDF network device symbols."""
from math import hypot


def shade(color, amount):
    values = [int(color[i:i+2], 16) for i in (1,3,5)]
    return '#' + ''.join(f'{round(v+(255-v)*amount if amount>=0 else v*(1+amount)):02x}' for v in values)


def arrow(start, end):
    x,y=start;dx,dy=end[0]-x,end[1]-y;length=hypot(dx,dy)
    ux,uy=dx/length,dy/length;px,py=-uy,ux
    bx,by=end[0]-5*ux,end[1]-5*uy
    points=((x+px,y+py),(bx+px,by+py),(bx+3*px,by+3*py),end,
            (bx-3*px,by-3*py),(bx-px,by-py),(x-px,y-py))
    return ('polygon', points, '#ffffff', None, 0)


def device_symbol(name, color):
    """Cylinder with routing arrows, switch block, or desktop monitor; no labels."""
    dark=shade(color,-.25);light=shade(color,.18)
    if name.startswith('R'):
        body=(('M',-31,-9),('L',-31,11),('C',-31,27,31,27,31,11),('L',31,-9),('Z',))
        return [('path',body,color,dark,1.2),('ellipse',0,-9,31,12,light,dark,1.2),
                arrow((-19,-16),(-4,-10)),arrow((4,-8),(19,-2)),
                arrow((19,-16),(4,-10)),arrow((-4,-8),(-19,-2))]
    if name.startswith('S'):
        return [('polygon',((-34,-14),(24,-14),(34,-5),(-24,-5)),light,dark,1.2),
                ('polygon',((-34,-14),(-24,-5),(-24,17),(-34,8)),dark,dark,1.2),
                ('polygon',((-24,-5),(34,-5),(34,17),(-24,17)),color,dark,1.2),
                arrow((-15,1),(23,1)),arrow((23,10),(-15,10))]
    return [('rect',-28,-21,56,35,2,dark,dark,1.2),
            ('rect',-24,-17,48,27,1,shade(color,.87),None,0),
            ('polygon',((-3,14),(3,14),(3,20),(14,20),(14,22),(-14,22),(-14,20),(-3,20)),dark,None,0)]


def symbol_svg(shapes):
    result=[]
    for kind,*values in shapes:
        *geometry,fill,stroke,width=values
        style=f'fill="{fill or "none"}" stroke="{stroke or "none"}" stroke-width="{width}" stroke-linejoin="round"'
        if kind=='ellipse':
            x,y,rx,ry=geometry;result.append(f'<ellipse cx="{x}" cy="{y}" rx="{rx}" ry="{ry}" {style}/>')
        elif kind=='rect':
            x,y,w,h,r=geometry;result.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" {style}/>')
        elif kind=='polygon':
            points=' '.join(f'{x},{y}' for x,y in geometry[0]);result.append(f'<polygon points="{points}" {style}/>')
        else:
            path=' '.join(command[0]+' '.join(str(v) for v in command[1:]) for command in geometry[0])
            result.append(f'<path d="{path}" {style}/>')
    return ''.join(result)


def draw_symbol(canvas, x, y, shapes):
    from reportlab.lib.colors import HexColor
    canvas.saveState();canvas.translate(x,y);canvas.scale(1,-1)
    for kind,*values in shapes:
        *geometry,fill,stroke,width=values
        if fill:canvas.setFillColor(HexColor(fill))
        if stroke:canvas.setStrokeColor(HexColor(stroke))
        canvas.setLineWidth(width);canvas.setLineJoin(1)
        style=dict(fill=int(bool(fill)),stroke=int(bool(stroke)))
        if kind=='ellipse':
            cx,cy,rx,ry=geometry;canvas.ellipse(cx-rx,cy-ry,cx+rx,cy+ry,**style)
        elif kind=='rect':
            px,py,w,h,r=geometry;canvas.roundRect(px,py,w,h,r,**style)
        else:
            path=canvas.beginPath()
            commands=geometry[0] if kind=='path' else [('M',*geometry[0][0])]+[('L',*point) for point in geometry[0][1:]]+[('Z',)]
            for operation,*points in commands:
                if operation=='M':path.moveTo(*points)
                elif operation=='L':path.lineTo(*points)
                elif operation=='C':path.curveTo(*points)
                elif operation=='Z':path.close()
            canvas.drawPath(path,**style)
    canvas.restoreState()
