"""Parse explicit calendar deadlines without guessing ambiguous numeric dates."""
import re
from datetime import datetime,date

def deadline_date(value):
    text=str(value or '').strip()
    if not text:return {'date':'','kind':'unknown'}
    if re.search(r'until filled|open until|rolling|ongoing',text,re.I):return {'date':'','kind':'rolling'}
    if re.search(r'review begins|review starts|priority|first consideration',text,re.I):return {'date':'','kind':'review','note':'Review/priority date; verify the final deadline.'}
    cleaned=re.sub(r'^(?:application\s+)?deadline\s*:\s*','',text,flags=re.I)
    iso=re.fullmatch(r'(\d{4}-\d{2}-\d{2})(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?',cleaned)
    if iso:
        try:return {'date':date.fromisoformat(iso[1]).isoformat(),'kind':'dated'}
        except ValueError:pass
    for fmt in ('%B %d, %Y','%b %d, %Y','%B %d %Y','%b %d %Y','%d %B %Y','%d %b %Y'):
        try:return {'date':datetime.strptime(cleaned,fmt).date().isoformat(),'kind':'dated'}
        except ValueError:pass
    return {'date':'','kind':'unknown','note':'Enter YYYY-MM-DD for deadline filtering and alerts.'}
