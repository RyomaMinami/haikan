"""Geometry helpers. Caller supplies verified model curves, dimensions and specification."""
import math
from xml.sax.saxutils import escape
from inventor_drawings import items
from workflow_helpers import checked_linear
START,END,CENTER=57857,57858,57860
HORIZONTAL,VERTICAL=60162,60163
LONG_DASH_DOT,CONTINUOUS=37642,37633

def third_angle_projection(doc,sheet,parent,point):
 doc.StylesManager.ActiveStandardStyle.FirstAngleProjection=False
 view=sheet.DrawingViews.AddProjectedView(parent,point,32260);doc.Update();return view

def center_crosses(sheet,view):
 """One mark per full circular center, not one per concentric outline."""
 found={}
 for curve in items(view.DrawingCurves):
  center=curve.CenterPoint
  if center is not None and curve.StartPoint is None:
   found.setdefault((round(center.X,6),round(center.Y,6)),curve)
 return {key:sheet.Centermarks.Add(sheet.CreateGeometryIntent(curve,CENTER)) for key,curve in found.items()}

def connect_centers(app,sheet,marks):
 """Pass endpoint centermarks of a verified straight hole row; no duplicate marks."""
 if len(marks)<2:raise ValueError('Single-hole rows have no connecting centerline')
 points=app.TransientObjects.CreateObjectCollection()
 for mark in marks:points.Add(mark)
 return sheet.Centerlines.Add(points)

def symmetry_axis(app,view,sheet_start,sheet_end,extension_mark_cm=.35):
 """View-attached sketch, NOT a persistent constraint on model symmetry.
 Coordinates are sheet cm, including extension beyond the actual contour.
 Caller must verify mirror geometry/specs and chosen dimensions after model changes.
 """
 x1,y1=sheet_start;x2,y2=sheet_end;length=math.hypot(x2-x1,y2-y1)
 if length<.5:raise ValueError('Axis too short for endpoint marks')
 ux,uy=(x2-x1)/length,(y2-y1)/length;px,py=-uy,ux;pt=app.TransientGeometry.CreatePoint2d
 sketch=view.Sketches.Add();sketch.Edit()
 def line(ax,ay,bx,by,kind):
  item=sketch.SketchLines.AddByTwoPoints(sketch.SheetToSketchSpace(pt(ax,ay)),sketch.SheetToSketchSpace(pt(bx,by)));item.LineType=kind;item.LineWeight=.018
 try:
  line(x1,y1,x2,y2,LONG_DASH_DOT)
  for x,y,sign in [(x1,y1,1),(x2,y2,-1)]:
   for offset in [.05,.25]:
    cx,cy=x+sign*offset*ux,y+sign*offset*uy;line(cx-extension_mark_cm*px,cy-extension_mark_cm*py,cx+extension_mark_cm*px,cy+extension_mark_cm*py,CONTINUOUS)
 finally:sketch.ExitEdit()
 return sketch

def linear_dimension(sheet,point,intent1,intent2,horizontal,expected_mm,*,center_clearance_cm=None):
 dim=checked_linear(sheet,point,intent1,intent2,HORIZONTAL if horizontal else VERTICAL,expected_mm,tolerance_mm=.001)
 dim.Style.TextStyle.FontSize=.32
 if expected_mm<10:dim.ArrowheadsInside=False
 if center_clearance_cm is not None:
  # OriginOffset affects both witnesses. Use only where both clearances fit.
  # Set from projected hole radius + cross extension + gap, NOT model radius alone.
  if not math.isfinite(center_clearance_cm) or center_clearance_cm<=0:
   dim.Delete();raise ValueError('Center clearance must be finite and positive')
  style=dim.Style.Copy('Center witness gap '+str(sheet.DrawingDimensions.GeneralDimensions.Count))
  style.OriginOffset=center_clearance_cm
  dim.Delete()
  # Inventor applies OriginOffset when placing the dimension, not retroactively.
  dim=checked_linear(sheet,point,intent1,intent2,HORIZONTAL if horizontal else VERTICAL,expected_mm,tolerance_mm=.001,dimension_style=style)
  if expected_mm<10:dim.ArrowheadsInside=False
 return dim

def projected_hole_axis(sheet,curve_one,curve_two):
 """Caller identifies the two parallel projected walls of the SAME hole."""
 return sheet.Centerlines.AddBisector(sheet.CreateGeometryIntent(curve_one),sheet.CreateGeometryIntent(curve_two))

def diameter_dimension(sheet,point,curve,expected_mm,*,suffix='',prefix='',precision=None):
 """Associated diameter, also for cut-extrusion circles without HoleFeatures.
 Through/depth/thread intent must be independently confirmed by the caller.
 Native DimensionValue already supplies the diameter symbol; do not prepend φ.
 """
 dim=sheet.DrawingDimensions.GeneralDimensions.AddDiameter(point,sheet.CreateGeometryIntent(curve))
 if abs(dim.ModelValue*10-expected_mm)>.001 or not dim.Attached or dim.ModelValueOverridden:
  dim.Delete();raise ValueError('Unverified diameter or detached geometry')
 dim.Style.TextStyle.FontSize=.32
 if precision is not None:dim.Precision=precision
 dim.Text.FormattedText=escape(prefix)+'<DimensionValue/>'+escape(suffix)
 dim.Text.Origin=point
 return dim

def outside_leader(app,sheet,curve,target_point,text_start,elbow,text):
 """Caller verifies actual circle arc, whole note box outside contour, short clear route."""
 pt=app.TransientGeometry.CreatePoint2d;points=app.TransientObjects.CreateObjectCollection();points.Add(pt(*text_start));points.Add(pt(*elbow));points.Add(sheet.CreateGeometryIntent(curve,target_point))
 return sheet.DrawingNotes.LeaderNotes.Add(points,'<StyleOverride FontSize="0.30">'+escape(text)+'</StyleOverride>')

def majority_roughness(app,sheet,x,y,ra_um):
 """Only apply explicitly selected profile/confirmed specification, on every sheet."""
 if ra_um is None:return None
 points=app.TransientObjects.CreateObjectCollection();points.Add(app.TransientGeometry.CreatePoint2d(x,y));symbol=sheet.SurfaceTextureSymbols.Add(points,76802,False,True);symbol.AdditionalRoughness='Ra '+str(ra_um);return symbol
