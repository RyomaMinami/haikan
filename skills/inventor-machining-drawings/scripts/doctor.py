"""Read-only checks; never starts/reconfigures/closes Inventor."""
import importlib.util,json,platform,sys
from pathlib import Path
def main():
 checks={'windows':sys.platform=='win32','python_3_11_plus':sys.version_info>=(3,11),'pywin32':importlib.util.find_spec('win32com') is not None,'pymupdf':importlib.util.find_spec('pymupdf') is not None,'bundled_template':(Path(__file__).resolve().parents[1]/'assets/templates/portable-a3.idw').is_file()}
 info={'checks':checks,'platform':platform.system(),'python':platform.python_version()}
 if checks['windows'] and checks['pywin32']:
  try:
   from inventor_drawings import connect
   app=connect();info['inventor_version']=app.SoftwareVersion.DisplayVersion;checks['running_inventor']=True
  except Exception as e:checks['running_inventor']=False;info['connection_error']=str(e)
 else:checks['running_inventor']=False
 print(json.dumps(info,ensure_ascii=False,indent=2));return 0 if all(checks.values()) else 1
if __name__=='__main__':raise SystemExit(main())
