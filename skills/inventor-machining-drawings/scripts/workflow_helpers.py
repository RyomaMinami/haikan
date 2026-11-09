"""Reusable local helpers; no model-specific curve indices or manufacturing assumptions."""
import json
import time
from pathlib import Path


class Timings:
    """Persist stage start before COM calls, including calls that stall or fail."""
    def __init__(self, path):
        self.path=Path(path)
        self.started=time.perf_counter()
        self.rows=[]

    def write(self):
        self.path.write_text(json.dumps({'elapsed_seconds':time.perf_counter()-self.started,
                                        'stages':self.rows},indent=2),encoding='utf-8')

    def run(self, name, fn, *args, **kwargs):
        row={'stage':name,'status':'running'};self.rows.append(row);self.write()
        start=time.perf_counter()
        try:
            value=fn(*args,**kwargs);row['status']='success';return value
        except Exception as exc:
            row.update(status='failed',error=repr(exc));raise
        finally:
            row['seconds']=time.perf_counter()-start;self.write()


def render_pdf(pdf, output):
    import pymupdf
    result=[]
    with pymupdf.open(pdf) as doc:
        for i,page in enumerate(doc,1):
            path=Path(output)/f'preview-{i}.png'
            page.get_pixmap(matrix=pymupdf.Matrix(1.5,1.5)).save(path)
            result.append(str(path))
    return {'pages':result,'visual_review':'PENDING: inspect every rendered page'}


def checked_linear(sheet, point, intent_one, intent_two, dimension_type, expected_mm, tolerance_mm=1e-5, dimension_style=None):
    """Caller must identify actual model geometry and substantiate expected_mm."""
    if dimension_style is None:
        dim=sheet.DrawingDimensions.GeneralDimensions.AddLinear(point,intent_one,intent_two,dimension_type)
    else:
        dim=sheet.DrawingDimensions.GeneralDimensions.AddLinear(point,intent_one,intent_two,dimension_type,True,dimension_style)
    if abs(dim.ModelValue*10-expected_mm)>tolerance_mm or not dim.Attached or dim.ModelValueOverridden:
        actual=dim.ModelValue*10
        dim.Delete()
        raise ValueError(f'Unverified linear dimension: expected {expected_mm}, measured {actual} mm')
    return dim


def add_hole_note(sheet, point, curve):
    note=sheet.DrawingNotes.HoleThreadNotes.Add(point,curve)
    note.Text.Origin=point  # Native initial placement may drift, particularly on opposite views.
    if not note.Attached or note.ModelValueOverridden:
        note.Delete();raise ValueError('Unattached or overridden hole note')
    return note
