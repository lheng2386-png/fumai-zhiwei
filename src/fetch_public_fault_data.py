"""Download explicitly identified public research data; retain original license."""
from pathlib import Path
import hashlib,json,urllib.request
ROOT=Path(__file__).resolve().parents[1]
MANIFEST = {'repository': 'clayton-h-costa/pv_fault_dataset', 'revision': 'e9ed8ad8072dcb3ffc643b0f3604219798708551', 'files': [{'name': 'dataset_amb.mat', 'url': 'https://raw.githubusercontent.com/clayton-h-costa/pv_fault_dataset/e9ed8ad8072dcb3ffc643b0f3604219798708551/dataset_amb.mat', 'bytes': 9933741, 'sha256': 'e4619812a5b6c1331332248f052cb09938f9a43ccac8bcc512796c6e906da7e0'}, {'name': 'dataset_elec.mat', 'url': 'https://raw.githubusercontent.com/clayton-h-costa/pv_fault_dataset/e9ed8ad8072dcb3ffc643b0f3604219798708551/dataset_elec.mat', 'bytes': 18143444, 'sha256': 'c04ed3fcc0e338703e87d387b1ce6ef51da1848e77b4d491e62afe08934ce494'}, {'name': 'LICENSE', 'url': 'https://raw.githubusercontent.com/clayton-h-costa/pv_fault_dataset/e9ed8ad8072dcb3ffc643b0f3604219798708551/LICENSE', 'bytes': 11357, 'sha256': 'c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4'}, {'name': 'README.md', 'url': 'https://raw.githubusercontent.com/clayton-h-costa/pv_fault_dataset/e9ed8ad8072dcb3ffc643b0f3604219798708551/README.md', 'bytes': 3014, 'sha256': '5204f6da3e825f093779e8df4f19f66efb7268a2ecb3a3ae81e298caac9e01ca'}]}
def main():
 out=ROOT/'data/raw/costa2020';out.mkdir(parents=True,exist_ok=True)
 for item in MANIFEST['files']:
  p=out/item['name']
  if not p.exists():
   with urllib.request.urlopen(item['url'],timeout=60) as r,p.open('wb') as f:
    while True:
     b=r.read(1024*1024)
     if not b:break
     f.write(b)
  if hashlib.sha256(p.read_bytes()).hexdigest()!=item['sha256']:
   raise ValueError(f'Checksum mismatch: {p.name}')
  print('Verified',p.name)
 (out/'manifest.json').write_text(json.dumps(MANIFEST,indent=2))
if __name__=='__main__':main()
