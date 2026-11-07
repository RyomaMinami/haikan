"""Create an empty generic A3 IDW from the recipient's own installed blank IDW."""
import argparse,json
from pathlib import Path
from types import SimpleNamespace
from inventor_drawings import connect,drawing_document,items,sha,add_draft_title_block
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source-template',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();source=a.source_template.resolve(strict=True);out=a.output.resolve()
 if out.exists():p.error('Output already exists')
 if out.suffix.lower()!='.idw':p.error('Output must be .idw')
 before=sha(source);app=connect();old=app.SilentOperation;doc=None
 try:
  app.SilentOperation=True;doc=drawing_document(app,app.Documents.Add(12292,str(source),False))
  if doc.Sheets.Count!=1 or doc.Sheets.Item(1).DrawingViews.Count or doc.ReferencedDocumentDescriptors.Count:raise ValueError('Source must be a blank single-sheet template without model references')
  sheet=doc.Sheets.Item(1);sheet.Size=9996;doc.StylesManager.ActiveStandardStyle.FirstAngleProjection=False
  if sheet.TitleBlock:sheet.TitleBlock.Delete()
  for note in items(sheet.DrawingNotes.GeneralNotes):note.Delete()
  for de in items(doc.TitleBlockDefinitions):
   try:de.Delete()
   except Exception:pass
  add_draft_title_block(app,doc,sheet,SimpleNamespace(FullFileName='未確定.ipt'),1)
  for name in ['Inventor Summary Information','Inventor Document Summary Information','Design Tracking Properties']:
   try:props=doc.PropertySets.Item(name)
   except Exception:continue
   for key in ['Author','Last Saved By','Company','Manager','Designer','Engineer','Checked By','Approved By','Description','Part Number','Project']:
    try:props.Item(key).Value=''
    except Exception:pass
  out.parent.mkdir(parents=True,exist_ok=True);doc.Update();doc.SaveAs(str(out),False)
  assert doc.ReferencedDocumentDescriptors.Count==0
 finally:
  if doc is not None:doc.Close(True)
  app.SilentOperation=old
  if sha(source)!=before:raise RuntimeError('Source changed')
 print(json.dumps({'output':out.name,'sha256':sha(out),'source_unchanged':True,'model_references':0},ensure_ascii=False))
if __name__=='__main__':main()
