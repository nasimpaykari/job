"""Optional UI-test server. Uses temporary data, never your active workspace."""
import sys, tempfile, os, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from coach.web import create_app
with tempfile.TemporaryDirectory(prefix='job-coach-ui-') as tmp:
    # A deterministic compiler stub tests studio behavior without network/TeX dependencies.
    import coach.pdf_engine
    def fake_compile(store,folder,log):
        from pypdf import PdfWriter
        writer=PdfWriter();writer.add_blank_page(width=612,height=792)
        with (folder/'document.pdf').open('wb') as output:writer.write(output)
        (folder/'compiler.log').write_text('UI test compiler stub')
        return {'engine':'test','pages':1,'approximate':False,'repairs':[]}
    coach.pdf_engine.compile_pdf=fake_compile
    if os.environ.get('POSTING_UI_TEST')=='1':
        import coach.freshness
        from coach.services import HTTPFailure
        counts={}
        def fake_fetch(url,settings):
            counts[url]=counts.get(url,0)+1
            if counts[url]>=3:raise HTTPFailure(403)
            changed=counts[url]==2
            body={'@type':'JobPosting','title':'Posting UI Faculty','description':'A robotics faculty position teaching computer science and conducting independent research. Submit a '+('three' if changed else 'two')+'-page research statement and cover letter.','validThrough':'2027-02-01' if changed else '2027-01-01'}
            return '<script type="application/ld+json">'+json.dumps(body)+'</script>',url
        coach.freshness.fetch=fake_fetch
    if os.environ.get('SEARCH_UI_TEST')=='1':
        import coach.services
        def fake_ai(self,prompt,system=''):
            if prompt.startswith('Suggest up to'):return json.dumps([{'name':'State Discovery College','state':'NJ','website':'https://state.example.edu'}])
            return json.dumps({'website':'https://state.example.edu','career_url':'https://state.example.edu/careers'})
        coach.services.AI.call=fake_ai
        coach.services.fetch=lambda url,settings:('<html><title>Employment opportunities</title><body>State Discovery College careers and faculty positions</body></html>',url)
    app=create_app(tmp,testing=True)
    sample_store=app.extensions['store']
    sample_store.add_job({'institution':'Example University','title':'Assistant Professor of Computer Science','url':'https://jobs.example.edu/positions/assistant-professor','description':'A sample position for testing the local interface.'})
    sample_store.add_job({'institution':'Sample Technology Company','title':'Research Scientist, Robotics','url':'https://careers.example.com/jobs/robotics-scientist','description':'A sample industry position for testing the local interface.'})
    if os.environ.get('SCHEDULER_UI_TEST')=='1':
        from coach.scheduler import Runner
        runner=Runner(tmp,lambda:[app.extensions['store']],notifier=lambda:'test desktop request')
        runner.start()
    app.run(host='127.0.0.1',port=5053,debug=False)
