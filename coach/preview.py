"""Readable content preview; never executes TeX or claims typesetting fidelity."""
import re

def readable_text(source):
    body=source.split(r'\begin{document}',1)[-1].split(r'\end{document}',1)[0]
    body=re.sub(r'(?<!\\)%[^\n]*','',body)
    body=re.sub(r'\\(?:input|include|includegraphics|bibliography)(?:\[[^\]]*\])?\{[^}]*\}', '[External content omitted]',body)
    body=re.sub(r'\\href\{([^}]*)\}\{([^}]*)\}',r'\2 (\1)',body)
    body=re.sub(r'\\(?:section|subsection|subsubsection|chapter)\*?\{([^}]*)\}',r'\n\n\1\n',body)
    body=re.sub(r'\\(?:begin|end)\{[^}]*\}', '\n',body)
    body=re.sub(r'\\item(?:\[([^]]*)\])?',lambda m:'\n• '+(m[1]+' ' if m[1] else ''),body)
    body=re.sub(r'\\(?:newpage|clearpage|par|newline)\b','\n\n',body)
    body=body.replace('\\\\','\n')
    # Preserve escaped literal characters before removing formatting commands.
    literals={}
    def literal(m):
        key=f'ZZLITERAL{len(literals)}ZZ';literals[key]=m[1];return key
    body=re.sub(r'\\([%&#_${}])',literal,body)
    body=re.sub(r'\\(?:vspace|hspace|setlength)\*?(?:\[[^]]*\])?\{[^}]*\}', '',body)
    body=re.sub(r'\\[A-Za-z@]+\*?(?:\[[^]]*\])?', '',body)
    body=body.replace('{','').replace('}','').replace('~',' ')
    for key,value in literals.items():body=body.replace(key,value)
    return re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+','\n\n',body).strip()
