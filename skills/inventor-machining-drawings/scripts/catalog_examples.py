"""Read-only inventory and optional PDF extraction/rendering. Output directory must be new."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',required=True,type=Path)
    p.add_argument('--examples',required=True,type=Path)
    p.add_argument('--output-dir',required=True,type=Path)
    p.add_argument('--render-pdf',action='append',type=Path,default=[])
    a=p.parse_args()
    root=a.root.resolve(strict=True); examples=a.examples.resolve(strict=True); out=a.output_dir.resolve()
    if out.exists() or any(x==out or x in out.parents for x in [root,examples]):
        p.error('Use a new output directory outside the source trees.')
    files=sorted(set([x for x in root.iterdir() if x.suffix.lower() in ('.idw','.dwg')]+
                     [x for x in examples.rglob('*') if x.suffix.lower() in ('.idw','.dwg')]))
    out.mkdir(parents=True)
    rows=[{'path':str(x),'bytes':x.stat().st_size,'sha256':hashlib.sha256(x.read_bytes()).hexdigest(),
           'historical':'OldVersions' in x.parts} for x in files]
    (out/'inventory.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    if a.render_pdf:
        import pymupdf
        pdf_rows=[]
        for index,path in enumerate(a.render_pdf,1):
            with pymupdf.open(path) as doc:
                pdf_rows.append({'path':str(path.resolve()),'pages':[page.get_text() for page in doc]})
                for number,page in enumerate(doc,1):
                    page.get_pixmap(matrix=pymupdf.Matrix(1.5,1.5)).save(str(out/f'pdf-{index}-page-{number}.png'))
        (out/'pdf-text.json').write_text(json.dumps(pdf_rows,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Inventor drawings inventoried: {len(rows)}')


if __name__=='__main__':
    main()
