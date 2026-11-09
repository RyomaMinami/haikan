"""Local Inventor COM: inspect drawings, export copies, create explicitly unfinished view drafts.
Requires Windows, running Inventor, Python 3, pywin32. Never saves input documents.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from xml.sax.saxutils import escape
from workflow_helpers import Timings, render_pdf
from drawing_layout import plan_views, scale_label, plain_formatted_text, readable_dimension

_INTERFACE_IDS = {}
_TIMINGS = None


def measured(name, fn, *args, **kwargs):
    return _TIMINGS.run(name, fn, *args, **kwargs) if _TIMINGS else fn(*args, **kwargs)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def items(collection):
    return [collection.Item(i) for i in range(1, collection.Count + 1)]


def connect():
    import pythoncom
    import win32com.client
    from win32com.client import dynamic
    win32com.client.Dispatch = lambda obj, userName=None, *a, **kw: dynamic.Dispatch(obj, userName)
    # Bypass broken generated pywin32 wrappers without deleting anyone's COM cache.
    return dynamic.Dispatch(pythoncom.GetActiveObject('Inventor.Application').QueryInterface(pythoncom.IID_IDispatch))


def drawing_document(app, doc):
    return cast_document(app, doc, 'DrawingDocument')


def cast_document(app, doc, interface):
    import pythoncom
    from win32com.client import dynamic
    lib, _ = app._oleobj_.GetTypeInfo().GetContainingTypeLib()
    key=(str(lib.GetLibAttr()),interface)
    if key in _INTERFACE_IDS:
        return dynamic.Dispatch(doc._oleobj_.QueryInterface(_INTERFACE_IDS[key],pythoncom.IID_IDispatch))
    for i in range(lib.GetTypeInfoCount()):
        ti = lib.GetTypeInfo(i)
        if ti.GetDocumentation(-1)[0] == interface:
            _INTERFACE_IDS[key]=ti.GetTypeAttr().iid
            return dynamic.Dispatch(doc._oleobj_.QueryInterface(_INTERFACE_IDS[key], pythoncom.IID_IDispatch))
    raise RuntimeError(interface+' interface unavailable')


def probe_model(app, doc):
    """Compact part evidence; no thumbnail access, B-rep dump, or inferred tolerances."""
    result={'path':doc.FullFileName,'type':doc.DocumentType,'scope':'parameters, native hole features and cylindrical-face evidence; machining/thread intent not inferred from geometry'}
    if doc.DocumentType!=12290:
        result['limitation']='Detailed probe currently supports IPT only';return result
    part=cast_document(app,doc,'PartDocument');cd=part.ComponentDefinition
    props=part.PropertySets.Item('Design Tracking Properties')
    result['properties']={name:str(props.Item(name).Value) for name in ['Part Number','Material','Description']}
    result['parameters']=[{'name':p.Name,'expression':p.Expression,'value_database_units':p.Value,'units':p.Units} for p in items(cd.Parameters)]
    result['holes']=[]
    for hole in items(cd.Features.HoleFeatures):
        row={'name':hole.Name,'tapped':bool(hole.Tapped),'extent_type':hole.ExtentType}
        try:
            row['depth_cm']=hole.Depth
            if hole.Tapped:
                tap=hole.TapInfo
                row.update(thread=tap.ThreadDesignation,thread_class=tap.Class,full_tap_depth=bool(tap.FullTapDepth),right_handed=bool(tap.RightHanded))
                if not tap.FullTapDepth:row['thread_depth_cm']=tap.ThreadDepth.Value
            else:row['diameter_cm']=hole.HoleDiameter.Value
        except Exception as exc:row['partial_read_error']=repr(exc)
        result['holes'].append(row)
    result['threads'] = []
    for feature in items(cd.Features.ThreadFeatures):
        thread = {'name': feature.Name, 'suppressed': bool(feature.Suppressed)}
        if not thread['suppressed']:
            try:
                info = feature.ThreadInfo
                thread.update(designation=info.ThreadDesignation, thread_class=info.Class,
                              right_handed=bool(info.RightHanded), full_depth=bool(feature.FullDepth))
                if not feature.FullDepth:
                    thread['length_cm'] = feature.ThreadDepth.Value
            except Exception as exc:
                thread['partial_read_error'] = repr(exc)
        result['threads'].append(thread)
    result['cylindrical_faces'] = cylindrical_faces(cd)
    result['geometric_hole_candidates'] = [r for r in result['cylindrical_faces']
                                          if r.get('interior') and r.get('full_circular_ends')]
    result['geometric_hole_scope'] = 'Candidates may duplicate native hole features. Do not sum both lists. Fillets/open grooves, intersecting holes, stepped/blind ends and threads need model review. Span is not drill or tap depth.'
    return result


def cylindrical_faces(cd):
    """B-rep evidence also covers cut extrusions/imports; not a toolpath detector."""
    rows = []
    for bi, body in enumerate(items(cd.SurfaceBodies), 1):
        for fi, face in enumerate(items(body.Faces), 1):
            if face.SurfaceType != 5891:
                continue
            row = {'body': bi, 'face': fi}
            try:
                cylinder = face.Geometry
                axis = cylinder.AxisVector
                base = cylinder.BasePoint
                p = face.PointOnFace
                _, normal = face.Evaluator.GetNormalAtPoint([p.X,p.Y,p.Z], [0.,0.,0.])
                direction = [axis.X,axis.Y,axis.Z]
                delta = [p.X-base.X,p.Y-base.Y,p.Z-base.Z]
                along = sum(delta[i]*direction[i] for i in range(3))
                radial = [delta[i]-along*direction[i] for i in range(3)]
                dot = sum(radial[i]*normal[i] for i in range(3))
                row.update(radius_mm=cylinder.Radius * 10,
                           diameter_mm=cylinder.Radius * 20,
                           axis_point_mm=[base.X*10, base.Y*10, base.Z*10],
                           axis_direction=[axis.X, axis.Y, axis.Z],
                           normal_dot_radial_cm=dot,
                           interior=dot < -1e-7)
                ends = []
                for edge in items(face.Edges):
                    if edge.GeometryType != 5124:  # full Circle, not Arc
                        continue
                    circle = edge.Geometry
                    center = circle.Center
                    openings = []
                    for adjacent in items(edge.Faces):
                        if adjacent.SurfaceType != 5890:
                            continue
                        for loop in items(adjacent.EdgeLoops):
                            if any(e.TransientKey == edge.TransientKey for e in items(loop.Edges)):
                                openings.append(not bool(loop.IsOuterEdgeLoop))
                    ends.append({'center_mm': [center.X*10, center.Y*10, center.Z*10],
                                 'radius_mm': circle.Radius*10,
                                 'planar_inner_loop_opening': any(openings)})
                row['circular_ends'] = ends
                row['full_circular_ends'] = len(ends) == 2 and all(abs(e['radius_mm']-row['radius_mm']) < 1e-5 for e in ends)
                row['through_status'] = 'unconfirmed'
                if row['full_circular_ends']:
                    a, b = [e['center_mm'] for e in ends]
                    row['axial_span_mm'] = abs(sum((b[i]-a[i])*row['axis_direction'][i] for i in range(3)))
                    if row['interior'] and all(e['planar_inner_loop_opening'] for e in ends):
                        row['through_status'] = 'two_planar_openings'
            except Exception as exc:
                row['partial_read_error'] = repr(exc)
            rows.append(row)
    return rows


def configure_drawing_font(doc, font='ＭＳ ゴシック'):
    """Only the generated document's local styles; never writes a style library."""
    for style in items(doc.StylesManager.TextStyles):
        if style.StyleLocation == 51203:
            style = style.ConvertToLocal()
        style.Font = font
    return font


def references(doc):
    result = []
    for r in items(doc.ReferencedDocumentDescriptors):
        result.append({'path': r.FullDocumentName, 'missing': bool(r.ReferenceMissing),
                       'suppressed': bool(r.ReferenceSuppressed)})
    return result


def inspect(doc):
    result = {'path': doc.FullFileName, 'type': doc.DocumentType,
              'references': references(doc), 'sheets': [], 'review_flags': [],
              'inspection_scope': 'GeneralDimensions and native HoleThreadNotes attachment/value overrides. Symbols, completeness and text overrides still need review. ModelValue uses database units (cm for lengths, radians for angles).'}
    if doc.DocumentType != 12292:
        raise ValueError('Expected an Inventor drawing document (IDW or Inventor DWG).')
    for s in items(doc.Sheets):
        row = {'name': s.Name, 'width_cm': s.Width, 'height_cm': s.Height,
               'views': [], 'general_notes': [], 'dimensions': [], 'hole_notes': []}
        for v in items(s.DrawingViews):
            row['views'].append({'name': v.Name, 'scale': v.Scale, 'width_cm': v.Width,
                                 'height_cm': v.Height,
                                 'position_cm': [v.Position.X, v.Position.Y],
                                 'parent': v.ParentView.Name if v.ParentView else None,
                                 'style': v.ViewStyle,
                                 'reference': v.ReferencedDocumentDescriptor.FullDocumentName})
        row['first_angle_projection'] = bool(doc.StylesManager.ActiveStandardStyle.FirstAngleProjection)
        row['general_notes_raw'] = []
        row['general_notes_formatted'] = []
        for n in items(s.DrawingNotes.GeneralNotes):
            row['general_notes_raw'].append(n.Text)
            row['general_notes_formatted'].append(n.FormattedText)
            row['general_notes'].append(plain_formatted_text(n.FormattedText, n.Text))
        for d in items(s.DrawingDimensions.GeneralDimensions):
            row['dimensions'].append({'text': readable_dimension(d.Text.Text, d.Text.FormattedText, d.Type == 117475328),
                                      'text_raw': d.Text.Text, 'formatted_text': d.Text.FormattedText,
                                      'model_value': d.ModelValue,
                                      'overridden': bool(d.ModelValueOverridden),
                                      'attached': bool(d.Attached)})
            if not d.Attached or d.ModelValueOverridden:
                result['review_flags'].append({'sheet':s.Name,'dimension_text':d.Text.Text,
                                              'issue':'detached or model-value override'})
        if s.TitleBlock:
            row['title_block'] = s.TitleBlock.Definition.Name
            row['title_texts'] = [s.TitleBlock.GetResultText(t) for t in items(s.TitleBlock.Definition.Sketch.TextBoxes)]
        for n in items(s.DrawingNotes.HoleThreadNotes):
            row['hole_notes'].append({'text':n.Text.Text,'attached':bool(n.Attached),'overridden':bool(n.ModelValueOverridden)})
            if not n.Attached or n.ModelValueOverridden:
                result['review_flags'].append({'sheet':s.Name,'hole_note':n.Text.Text,'issue':'detached or model-value override'})
        result['sheets'].append(row)
    return result


def export_pdf(app, doc, path):
    old=app.SilentOperation
    try:
        app.SilentOperation=True
        return _export_pdf(app,doc,path)
    finally:
        app.SilentOperation=old


def _export_pdf(app, doc, path):
    addin = app.ApplicationAddIns.ItemById('{0AC6FD96-2F4D-42CE-8BE0-8AEA580399E4}')
    if not addin.Activated:
        addin.Activate()
    ctx = app.TransientObjects.CreateTranslationContext()
    ctx.Type = 13059
    opts = app.TransientObjects.CreateNameValueMap()
    if not addin.HasSaveCopyAsOptions(doc, ctx, opts):
        raise RuntimeError('PDF export options unavailable')
    # Replace entries explicitly; Add alone rejects duplicate keys in Inventor 2024.
    for key,value in [('All_Color_AS_Black',0),('Sheet_Range',14082),('Vector_Resolution',400)]:
        if key in [opts.Name(i) for i in range(1,opts.Count+1)]:
            opts.Remove(key)
        opts.Add(key,value)
    medium = app.TransientObjects.CreateDataMedium()
    medium.FileName = str(path)
    addin.SaveCopyAs(doc, ctx, opts, medium)
    if not path.is_file() or path.stat().st_size < 100:
        raise RuntimeError('PDF export did not create a nonempty file')


def add_draft_title_block(app, doc, sheet, model, scale, *,
                          status='未完成・加工不可', drawing_number=None,
                          sheet_number='1 / 1', font='ＭＳ ゴシック'):
    """Native Inventor title block; unknown specifications remain explicit."""
    pt = app.TransientGeometry.CreatePoint2d
    definition = doc.TitleBlockDefinitions.Add('Portable Draft ' + str(doc.TitleBlockDefinitions.Count + 1))
    sketch = definition.Edit()
    sketch.SketchLines.AddAsTwoPointRectangle(pt(0,0),pt(18,3))
    for y in [1,2]:
        sketch.SketchLines.AddByTwoPoints(pt(0,y),pt(18,y))
    sketch.SketchLines.AddByTwoPoints(pt(9,0),pt(9,2))
    if sheet.TitleBlock:
        sheet.TitleBlock.Delete()
    cells=[(.3,2.75,'図番：'+(drawing_number or '未確定')+' / '+Path(model.FullFileName).stem),
           (.3,1.75,'材質：未確定'),(9.3,1.75,'数量：未確定'),
           (.3,.75,'尺度 '+scale_label(scale)),(9.3,.75,sheet_number+'  '+status)]
    for x,y,text in cells:
        sketch.TextBoxes.AddFitted(pt(x,y),'<StyleOverride Font="'+escape(font, {'"':'&quot;'})+'" FontSize="0.3">'+escape(text)+'</StyleOverride>')
    definition.ExitEdit(True)
    sheet.AddTitleBlock(definition,29444)  # kBottomRightPosition


def draft(app, model, template, scale, front, output, view_names=None,
          include_iso=False, edge_note=''):
    doc = measured('create_drawing', lambda: drawing_document(app, app.Documents.Add(12292, str(template), False)))
    try:
        if doc.Sheets.Count != 1 or doc.Sheets.Item(1).DrawingViews.Count:
            raise ValueError('Use a blank single-sheet drawing template, not a submitted drawing.')
        sheet = doc.Sheets.Item(1)
        sheet.Size = 9996  # A3; actual dimensions read below, so orientation is not assumed.
        if sheet.TitleBlock:
            sheet.TitleBlock.Delete()  # Avoid inherited material/author/quantity/title claims.
        for note in items(sheet.DrawingNotes.GeneralNotes):
            note.Delete()
        w, h = sheet.Width, sheet.Height
        tg = app.TransientGeometry
        pt = tg.CreatePoint2d
        configure_drawing_font(doc)
        doc.StylesManager.ActiveStandardStyle.FirstAngleProjection = False
        orientations={'front':front,'top':10754,'right':10755,'back':10756,'bottom':10757,'left':10758,'iso':10759}
        selected=list(view_names or ['front','top'])
        if include_iso:selected.append('iso')
        if len(selected) > 1 and 'front' not in selected:
            raise ValueError('Multiple orthographic views require front as the projection parent')
        views={}
        root_name = 'front' if 'front' in selected else selected[0]
        parent=measured('create_view_'+root_name,sheet.DrawingViews.AddBaseView,model,pt(w/2,h/2),scale,orientations[root_name],32257)
        parent.Name=root_name.upper();views[root_name]=parent
        doc.Update()
        offsets={'top':(0,10),'bottom':(0,-10),'right':(10,0),'left':(-10,0)}
        for name in selected:
            if name==root_name:continue
            if name in offsets:
                dx,dy=offsets[name]
                v=measured('create_view_'+name,sheet.DrawingViews.AddProjectedView,parent,pt(w/2+dx,h/2+dy),32260)
            else:
                # BACK and ISO are independent orientations; no false projection parent.
                v=measured('create_view_'+name,sheet.DrawingViews.AddBaseView,model,pt(w/2+10,h/2+10),scale,orientations[name],32258 if name=='iso' else 32257)
            v.Name=name.upper();views[name]=v
        measured('update_initial_views',doc.Update)
        sizes={name:(v.Width/scale,v.Height/scale) for name,v in views.items()}
        actual,positions=plan_views(sizes,scale,(2,8,w-2,h-6))
        for name,v in views.items():
            if not v.ParentView:v.Scale=actual
        measured('update_scale',doc.Update)
        parent.Position=pt(*positions[root_name])
        for name,v in views.items():
            if name!=root_name:v.Position=pt(*positions[name])
        measured('update_positions',doc.Update)
        for name,v in views.items():
            sheet.DrawingNotes.GeneralNotes.AddFitted(pt(v.Position.X-v.Width/2,v.Position.Y-v.Height/2-.5),name.upper())
        text='未完成・加工不可 / VIEW DRAFT ONLY\n'+Path(model.FullFileName).name+'\n寸法・材質・公差・数量・加工工程は未確定。製作には使用しない。'
        sheet.DrawingNotes.GeneralNotes.AddFitted(pt(1.2,h-1.2),escape(text))
        sheet.DrawingNotes.GeneralNotes.AddFitted(pt(1.2,5.5),escape('第三角法 / 単位 mm / 正面方向と必要な加工面を要確認'))
        if edge_note:
            sheet.DrawingNotes.GeneralNotes.AddFitted(pt(1.2,4.5),escape(edge_note))
        measured('title_block',add_draft_title_block,app,doc,sheet,model,actual)
        measured('update_annotations',doc.Update)
        measured('save_idw',doc.SaveAs,str(output/'view-draft.idw'), False)
        measured('export_pdf',export_pdf,app,doc,output/'view-draft.pdf')
        result=measured('inspect_output',inspect,doc)
        result.update(status='VIEW_DRAFT_NOT_FOR_MANUFACTURE',scale=actual,scale_label=scale_label(actual),selected_views=selected,edge_note=edge_note,
                      font='ＭＳ ゴシック',projection='third-angle native parent/projected views; ISO without hidden lines',
                      omitted=['manufacturing dimensions','hole/thread notes','material approval','tolerances','quantity','sections','process selection'])
        return result
    finally:
        measured('close_generated_document',doc.Close,True)


def main():
    global _TIMINGS
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['inspect','export','draft'])
    p.add_argument('--input',required=True,type=Path)
    p.add_argument('--output-dir',required=True,type=Path,help='Must not exist; reserved for this run')
    p.add_argument('--template',type=Path)
    p.add_argument('--scale',type=float,default=1)
    p.add_argument('--views',nargs='+',choices=['front','top','right','back','bottom','left'],default=['front','top'],help='Select only necessary orthographic views; default front top')
    p.add_argument('--include-iso',action='store_true',help='Add an isometric view only when specifically needed/requested; never the default')
    p.add_argument('--edge-note',default='',help='Only use a confirmed shared note; neutral default is empty')
    p.add_argument('--probe-model',action='store_true',help='Collect compact model evidence in the same COM session (draft only)')
    p.add_argument('--render-pdf',action='store_true',help='Render every exported page locally; human/model visual review is still required')
    args=p.parse_args()
    source=args.input.resolve(strict=True)
    out=args.output_dir.resolve()
    if out.exists():
        p.error('Output directory must be new; no overwrite is permitted.')
    if out==source.parent or source.parent in out.parents:
        p.error('Choose an output location outside the input directory tree.')
    if not math.isfinite(args.scale) or args.scale<=0:
        p.error('Scale must be finite and positive.')
    if len(args.views)!=len(set(args.views)):
        p.error('Duplicate view names are not permitted.')
    if args.mode=='draft' and (not args.template or source.suffix.lower() not in ('.ipt','.iam')):
        p.error('Draft requires an IPT/IAM and --template blank IDW/DWG.')
    if args.mode!='draft' and source.suffix.lower() not in ('.idw','.dwg'):
        p.error('Inspection/export requires IDW or DWG.')
    template=args.template.resolve(strict=True) if args.template else None
    out.mkdir(parents=True)
    _TIMINGS=Timings(out/'timings.json')
    app=measured('connect',connect)
    def inventory():
        found={}
        for d in items(app.Documents):
            filename=d.FullFileName
            if filename:found[str(Path(filename).resolve()).casefold()]=d
        return found
    existing=measured('open_document_inventory',inventory)
    doc=existing.get(str(source).casefold())
    if doc is not None and doc.Dirty:
        raise RuntimeError('Input is already open with unsaved changes; choose a saved snapshot.')
    protected={str(source):measured('hash_input',sha,source)}
    if template:
        protected[str(template)]=sha(template)
    report={'mode':args.mode,'input':str(source),'version':app.SoftwareVersion.DisplayVersion,'status':'FAILED',
            'release_status':'NOT_REVIEWED_FOR_MANUFACTURE',
            'warning':'Opening can resolve current models; PDF export is not guaranteed to reproduce a historical submitted PDF.'}
    old_silent=app.SilentOperation
    owned=doc is None
    try:
        app.SilentOperation=True
        if owned:
            doc=measured('open_input',app.Documents.Open,str(source),False)
        for d in items(doc.AllReferencedDocuments):
            if d.FullFileName and Path(d.FullFileName).is_file():
                protected[d.FullFileName]=sha(d.FullFileName)
            if d.Dirty and str(Path(d.FullFileName).resolve()).casefold() in existing:
                raise RuntimeError('A referenced document has unsaved changes: '+d.FullFileName)
        refs=references(doc)
        report['references']=refs
        if args.mode!='inspect' and any(r['missing'] and not r['suppressed'] for r in refs):
            raise RuntimeError('Unresolved reference; export/draft stopped. Inspection report remains available.')
        if args.mode=='draft':
            if args.probe_model:
                report['model_evidence']=measured('probe_model',probe_model,app,doc)
            report['drawing']=draft(app,doc,template,args.scale,10764,out,args.views,args.include_iso,args.edge_note)
        else:
            doc=drawing_document(app,doc)
            report['drawing']=measured('inspect_input',inspect,doc)
            if args.mode=='export':
                measured('export_pdf',export_pdf,app,doc,out/'drawing.pdf')
        if args.render_pdf and args.mode in ('draft','export'):
            report['render']=measured('render_all_pages',render_pdf,out/('view-draft.pdf' if args.mode=='draft' else 'drawing.pdf'),out)
        report['status']='SUCCESS'
    except Exception as e:
        report['error']=repr(e)
        raise
    finally:
        try:
            if owned and doc is not None:
                measured('close_input',doc.Close,True)  # SkipSave=True; leave user documents open.
        finally:
            app.SilentOperation=old_silent
            report['source_hashes']=measured('verify_source_hashes',lambda:[{'path':path,'before':before,'after':sha(path)} for path,before in protected.items()])
            report['sources_unchanged']=all(x['before']==x['after'] for x in report['source_hashes'])
            (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            if not report['sources_unchanged']:
                raise RuntimeError('Input hash changed during run; inspect report before proceeding.')
    print(json.dumps({'status':report['status'],'output':str(out)},ensure_ascii=False))


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    main()
