"""Small allowlist for worksheet formatting, shared by storage and PDF export."""
from html import escape, unescape
from html.parser import HTMLParser
import re

TAGS = {'p','div','br','b','strong','i','em','u','ul','ol','li','h2','h3','blockquote'}
DROP = {'script','style','iframe','object','svg','math','template'}


class RichText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[];self.dropped=0;self.stack=[]

    def handle_starttag(self, tag, attrs):
        if tag in DROP:self.dropped+=1;return
        if self.dropped or tag not in TAGS:return
        if tag=='br':self.parts.append('<br>');return
        self.stack.append(tag)
        self.parts.append('<'+tag+'>')

    def end(self, tag):
        self.parts.append('</'+tag+'>')

    def handle_endtag(self, tag):
        if tag in DROP:
            self.dropped=max(0,self.dropped-1);return
        if self.dropped or tag not in self.stack:return
        while self.stack:
            current=self.stack.pop();self.end(current)
            if current==tag:break

    def handle_data(self, data):
        if not self.dropped:self.parts.append(escape(data))

    def result(self):
        while self.stack:self.end(self.stack.pop())
        return ''.join(self.parts)


def rich_html(value):
    if not re.search(r'</?[a-zA-Z][^>]*>',value):
        value=value.replace('\n','<br>')
    parser=RichText();parser.feed(value);return parser.result()


def plain_text(value):
    value = re.sub(r'<br>|</(?:p|div|li|h2|h3)>', '\n', rich_html(value))
    return unescape(re.sub(r'<[^>]*>', '', value)).strip()


class BlockTree(HTMLParser):
    """Parse canonical allowlisted HTML into block and inline nodes."""
    def __init__(self, value):
        super().__init__(convert_charrefs=True)
        self.root=dict(tag='root',children=[]);self.stack=[self.root]
        self.feed(rich_html(value))

    def handle_starttag(self, tag, attrs):
        node=dict(tag=tag,children=[]);self.stack[-1]['children'].append(node)
        if tag!='br':self.stack.append(node)

    def handle_endtag(self, tag):
        if len(self.stack)>1 and self.stack[-1]['tag']==tag:self.stack.pop()

    def handle_data(self, value):
        self.stack[-1]['children'].append(escape(value))


def pdf_blocks(value):
    """Return paragraphs with list depth/marker, retaining safe inline markup."""
    blocks=[]
    def inline(node):
        if isinstance(node,str):return node
        tag=node['tag'];content=''.join(inline(child) for child in node['children'])
        if tag=='br':return '<br/>'
        mapped={'b':'b','strong':'b','i':'i','em':'i','u':'u'}.get(tag)
        return '<'+mapped+'>'+content+'</'+mapped+'>' if mapped else content
    def walk(nodes,depth=0,marker=None,heading=''):
        buffer=[]
        def flush():
            nonlocal marker
            content=''.join(buffer).strip();buffer.clear()
            if content:
                blocks.append(dict(html=content,depth=depth,marker=marker,heading=heading));marker=None
        for node in nodes:
            tag=node['tag'] if isinstance(node,dict) else ''
            if tag in ('ol','ul'):
                flush()
                items=[child for child in node['children'] if isinstance(child,dict) and child['tag']=='li']
                for number,item in enumerate(items,1):
                    walk(item['children'],depth+1,str(number)+'.' if tag=='ol' else '•')
            elif tag in ('p','div','h2','h3','blockquote'):
                flush();before=len(blocks)
                walk(node['children'],depth+(tag=='blockquote'),marker,tag if tag in ('h2','h3') else heading)
                if len(blocks)>before:marker=None
            else:buffer.append(inline(node))
        flush()
    walk(BlockTree(value).root['children'])
    return blocks


def pdf_flowables(value, base_style):
    """Real paragraphs provide hanging list indents and reliable page splitting."""
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    result=[]
    for block in pdf_blocks(value):
        indent=block['depth']*18
        heading=bool(block['heading'])
        style=ParagraphStyle('LABRichBlock',parent=base_style,leftIndent=indent,
                             firstLineIndent=0,bulletIndent=max(0,indent-18),
                             bulletFontName=base_style.fontName,bulletFontSize=base_style.fontSize,
                             spaceAfter=3 if block['marker'] else 5,
                             spaceBefore=5 if heading else 0,keepWithNext=heading)
        html=block['html']
        if heading:html='<b>'+html+'</b>'
        result.append(Paragraph(html,style,bulletText=block['marker']))
    return result
