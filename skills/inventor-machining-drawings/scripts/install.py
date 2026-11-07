"""Install the whole skill without replacing an existing installation."""
import argparse,shutil
from pathlib import Path
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--target',choices=['claude','codex'],required=True);p.add_argument('--project',type=Path);a=p.parse_args()
 src=Path(__file__).resolve().parents[1];base=a.project.resolve(strict=True) if a.project else Path.home()
 if not base.is_dir():p.error('Project must be an existing directory')
 dest=base/('.claude' if a.target=='claude' else '.codex')/'skills'/src.name
 if dest.exists():p.error('Destination exists; installation stopped: '+str(dest))
 if src==dest or src in dest.parents:p.error('Destination cannot be within the source package')
 shutil.copytree(src,dest,ignore=shutil.ignore_patterns('__pycache__','*.pyc','.git','.venv'));print(dest)
if __name__=='__main__':main()
