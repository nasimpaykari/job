"""Narrow compilation-only repairs; the user's original document remains intact."""
import re

def prepare_latex(source):
    notes=[]
    match=re.search(r'\\documentclass[\s\S]*?\\end\{document\}',source)
    text=(source[:match.start()] if source[:match.start()].strip().startswith('\\') else '')+match.group(0) if match else source
    if text!=source:notes.append('Removed text outside the LaTeX document for compilation.')
    preamble=text.split(r'\begin{document}',1)[0]
    uncommented=re.sub(r'(?<!\\)%[^\n]*','',preamble)
    packages=[]
    for m in re.finditer(r'\\(?:usepackage|RequirePackage)(?:\s*\[[^]]*\])?\s*\{([^}]+)\}',uncommented):packages.extend(x.strip() for x in m[1].split(','))
    if 'FiraSans' in packages and 'XCharter' in packages:
        # XCharter's older XeTeX branch requests fontspec with no-math, while
        # FiraSans loads it earlier without that option. Declare it before either.
        text='\\PassOptionsToPackage{no-math}{fontspec}\n'+text
        notes.append('Predeclared fontspec no-math for FiraSans/XCharter compatibility; body and fonts preserved.')
    return text,notes
