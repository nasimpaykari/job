"""Optional real browser PDF pipeline smoke test using synthetic candidate facts."""
from pathlib import Path
import tempfile,re,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from coach.core import Store
from coach.services import Coach
from coach.career import Career
from coach.packages import Packages
from pypdf import PdfReader
with tempfile.TemporaryDirectory() as tmp:
 s=Store(tmp);s.save_settings({'pdf_engine':'browser'})
 j=s.add_job({'title':'Software Engineer','institution':'Example Robotics Inc','career_track':'Industry'})[0]
 source=(ROOT/'templates'/'industry_resume.tex').read_text()
 replacements=['Jordan Example','Newark, NJ','jordan@example.com','example.com','Software engineer with Python and robotics project experience.','Python, ROS, Git','Research Assistant','Example Laboratory','May 2024 to Present','Developed navigation software using Python and ROS.','Tested route recovery in simulation.','Controls Engineer','Example Systems','January 2020 to April 2024','Supported industrial control commissioning.','Navigation study','Implemented and tested robot navigation methods.','MS in Computer Science','Example University','May 2024']
 body=source.split('\\begin{document}',1)[1];it=iter(replacements);body=re.sub(r'\\placeholder\{[^}]*\}',lambda _:next(it),body);source=source.split('\\begin{document}',1)[0]+'\\begin{document}'+body
 coach=Coach(s);preview=coach.preview_document('a'*32,j,'Resume',source)
 assert preview['checks']['errors']==0
 approved=coach.approve_preview('a'*32,j,'Resume',source,review_ack=True)
 path=s.document_version(approved['id'])/'document.pdf';text=' '.join(p.extract_text() or '' for p in PdfReader(path).pages)
 assert len(PdfReader(path).pages)==1
 for expected in ['Jordan Example','Work Experience','Skills','Selected Projects','Education','Python']:assert expected in text,(expected,text)
 Career(s).review_checklist(j);package=Packages(s).create(j,{'document_ids':[approved['id']],'acknowledge_warnings':True});assert Packages(s).download(package['id']).exists()
 assert not (Path(coach.preview_root.name)/('a'*32)).exists()
 print('Real Chromium PDF end-to-end passed: one page, readable section/contact text, approval, package ZIP, temporary preview removed.',flush=True)
 (Path(tempfile.gettempdir())/'job-coach-resume-smoke.pdf').write_bytes(path.read_bytes())
