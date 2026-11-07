"""Portable CLI adapter; profile does not invent manufacturing specifications."""
import argparse,json,subprocess,sys
from pathlib import Path
def main():
 root=Path(__file__).resolve().parents[1];p=argparse.ArgumentParser(description=__doc__);p.add_argument('--profile',default='default');a,rest=p.parse_known_args()
 profile=root/'profiles'/(a.profile+'.json')
 if profile.resolve().parent!=(root/'profiles').resolve():p.error('Profile must be a bundled profile name')
 data=json.loads(profile.read_text(encoding='utf-8'));cmd=[sys.executable,str(root/'scripts/inventor_drawings.py'),*rest]
 if 'draft' in rest:
  if '--template' not in rest:cmd.extend(['--template',str(root/'assets/templates/portable-a3.idw')])
  if '--edge-note' not in rest:cmd.extend(['--edge-note',data.get('edge_note') or ''])
 return subprocess.run(cmd).returncode
if __name__=='__main__':raise SystemExit(main())
