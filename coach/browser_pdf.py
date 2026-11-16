"""Explicitly approximate browser fallback adapted from the supplied application."""
import re,html
from datetime import date
from bs4 import BeautifulSoup
def latex_to_clean_html_for_pdf(tex: str) -> str:
    tex = html.escape(tex, quote=True)
    body = tex.split('\\begin{document}')[1] if '\\begin{document}' in tex else tex
    body = body.split('\\end{document}')[0] if '\\end{document}' in body else body
    body = re.sub(r'\{\\LARGE\\bfseries(?:\\color\{[^}]*\})?\s*([^{}]*)\}',r"<h1 style='font:700 21pt Arial,sans-serif;color:#163D64;margin:0 0 4px;'>\1</h1>",body)
    body = re.sub(r'\\par\b', '<br>', body)
    body = re.sub(r'(?<!\\)%[^\n]*', '', body)
    body = re.sub(r'\\(?:ressection|rssection|teachingsection|divsection|mentoringsection|section|subsection)\*?\{([^{}]*)\}', r"<h2 style='font:700 13pt Arial,sans-serif;color:#163D64;margin:12px 0 5px;'>\1</h2>", body)
    def list_markup(match):
        items=re.split(r'\\item(?:\[[^]]*\])?\s*',match[2])[1:]
        tag='ol' if match[1]=='enumerate' else 'ul'
        return '<'+tag+" style='margin:4px 0 8px;padding-left:18px;'>"+''.join('<li>'+x.strip()+'</li>' for x in items)+'</'+tag+'>'
    body = re.sub(r'\\begin\{(itemize|enumerate)\}(.*?)\\end\{\1\}',list_markup,body,flags=re.S)
    body = re.sub('\\\\color\\{[^}]+\\}', ' ', body)
    body = re.sub('\\\\vspace\\{[^}]+\\}', "<div style='height:8px;'></div>", body)
    body = re.sub('\\\\noindent\\s*', '', body)
    body = re.sub('\\\\today', date.today().isoformat(), body)
    body = re.sub('\\\\rule\\{[^}]+\\}\\{[^}]+\\}', "<hr style='border:none;border-top:1.5px solid #18698a;margin:8px 0;'>", body)
    body = re.sub('\\\\textbf\\{\\\\textcolor\\{[^}]+\\}\\{([^}]+)\\}\\}', "<strong style='color:#122440;'>\\1</strong>", body)
    body = re.sub('\\\\textcolor\\{AccentTeal\\}\\{\\\\textbf\\{([^}]+)\\}\\}', "<strong style='color:#18698a;'>\\1</strong>", body)
    body = re.sub('\\\\textcolor\\{[^}]+\\}\\{([^}]+)\\}', '<span>\\1</span>', body)
    body = re.sub('\\\\textbf\\{([^}]+)\\}', '<strong>\\1</strong>', body)
    body = re.sub('\\\\textit\\{([^}]+)\\}', '<em>\\1</em>', body)
    body = re.sub('\\\\href\\{([^}]+)\\}\\{([^}]+)\\}', "<a href='\\1' style='color:#18698a;text-decoration:none;'>\\2</a>", body)
    body = re.sub('\\\\begin\\{center\\}([\\s\\S]*?)\\\\end\\{center\\}', "<div style='text-align:center;margin-bottom:10px;'>\\1</div>", body)
    body = re.sub('\\\\\\\\(?:\\[\\d+pt\\])?', '<br>', body)
    body = re.sub('\\$\\\\cdot\\$', ' &middot; ', body)
    body = re.sub('\\\\&', '&amp;', body)
    body = re.sub('\\\\%', '%', body)
    body = re.sub(r'\\(?:setlength|addvspace|hspace|vspace)\*?(?:\[[^]]*\])?\{[^}]*\}', '', body)
    body = re.sub(r'\\[a-zA-Z]+\*?(?:\[[^]]*\])?', '', body)
    body = body.replace('{', '').replace('}', '')
    paragraphs = [f"<p style='margin:0 0 10px 0;text-align:justify;'>{p.strip()}</p>" for p in re.split('\\n\\s*\\n', body) if p.strip()]
    return "<!DOCTYPE html><html><head><meta charset='UTF-8'><style>@page { size: letter; margin: 0.65in; } body { font-family: Arial, Helvetica, sans-serif; font-size: 11pt; line-height: 1.32; color: #23262c; margin: 0; }</style></head><body>" + ''.join(paragraphs) + '</body></html>'

def render_pdf(tex,destination):
    try:from playwright.sync_api import sync_playwright
    except ImportError:raise ValueError('Install the browser PDF component in settings first.') from None
    markup=latex_to_clean_html_for_pdf(tex)
    soup=BeautifulSoup(markup,'html.parser')
    for a in soup.find_all('a'):
        if not str(a.get('href','')).startswith(('https://','http://','mailto:')):a.attrs.pop('href',None)
    with sync_playwright() as p:
        browser=None
        for channel in [None,'msedge','chrome']:
            try:
                browser=p.chromium.launch(headless=True,**({'channel':channel} if channel else {}));break
            except Exception:pass
        if browser is None:raise ValueError('No usable PDF browser found. Install the browser PDF component or install Microsoft Edge/Chrome.')
        try:
            page=browser.new_page(java_script_enabled=False)
            page.route('**/*',lambda route:route.abort())
            page.set_content(str(soup),wait_until='domcontentloaded')
            page.pdf(path=str(destination),format='Letter',print_background=True,display_header_footer=True,header_template='<div></div>',footer_template='<div style="font-size:8px;text-align:center;width:100%">Approximate browser layout · not a LaTeX typeset PDF</div>',margin={'top':'0.65in','bottom':'0.65in','left':'0.68in','right':'0.68in'})
        finally:browser.close()
