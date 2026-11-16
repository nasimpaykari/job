from datetime import date
import pytest
from coach.core import Store
from coach.deadlines import deadline_date
from coach.search_plan import build_plan,selection_reasons
from coach.universities import list_universities,update_university

def target(kind,value,enabled=True):return {'id':kind+str(len(value)),'kind':kind,'label':value,'value':value,'enabled':enabled}

@pytest.mark.parametrize('text,kind,result',[
 ('2026-11-01','dated','2026-11-01'),('2026-11-01T23:59:00-05:00','dated','2026-11-01'),
 ('November 1, 2026','dated','2026-11-01'),('Deadline: Nov 1, 2026','dated','2026-11-01'),
 ('2026-02-30','unknown',''),('11/01/2026','unknown',''),('', 'unknown',''),
 ('Open until filled','rolling',''),('Priority review begins November 1, 2026','review','')])
def test_deadline_parser(text,kind,result):
    assert deadline_date(text)['kind']==kind and deadline_date(text)['date']==result

def test_settings_validation_and_persistence(tmp_path):
    s=Store(tmp_path);s.save_settings({'deadline_warning_days':7,'deadline_alerts':False,'search_targets':[target('state','NY')],'saved_search_enabled':True})
    cfg=Store(tmp_path).settings();assert cfg['deadline_warning_days']==7 and cfg['search_targets'][0]['enabled']
    for d in ({'deadline_warning_days':0},{'deadline_alerts':'yes'},{'search_targets':[target('url','file:///secret')]},{'search_targets':[target('role','Invented')]}):
        with pytest.raises(ValueError):s.save_settings(d)

def test_checked_sources_queries_and_states(tmp_path):
    source='https://jobs.example.org/search?q={query}&loc={location}'
    s=Store(tmp_path);s.save_settings({'saved_search_enabled':True,'search_targets':[target('board',source),target('board','CRA',False),target('state','NY'),dict(target('state','CA'),id='stateCA'),target('job','robotics')]})
    effective,urls,selected=build_plan(s,s.settings(),'both')
    assert len(urls)==2 and all(x[0]==source and 'robotics' in x[1] for x in urls)
    assert effective['regions']==['NY','CA']
    assert selection_reasons({'title':'Python teacher','description':''},selected)
    assert not selection_reasons({'title':'Robotics scientist','description':''},selected)

def test_all_sources_unchecked_does_not_search_everything(tmp_path):
    s=Store(tmp_path);s.save_settings({'saved_search_enabled':True,'search_targets':[target('board','CRA',False),target('state','NY')]})
    with pytest.raises(ValueError,match='No sources'):build_plan(s,s.settings(),'both')

def test_custom_board_url_and_keywords(tmp_path):
    s=Store(tmp_path);s.save_settings({'saved_search_enabled':True,'search_targets':[target('board','https://example.edu/jobs?q={query}&state={location}'),target('job','C++'),target('state','NJ'),target('keyword','teaching')]})
    eff,urls,sel=build_plan(s,s.settings(),'both')
    assert len(urls)==1 and 'C%2B%2B' in urls[0][1] and 'NJ' in urls[0][1]
    assert 'teaching' in eff['required_keywords']
    assert selection_reasons({'title':'C# teaching'},sel)
    assert not selection_reasons({'title':'C++ teaching'},sel)

def test_selected_university_requires_confirmation_and_filters_board_results(tmp_path):
    s=Store(tmp_path);s.add_job({'institution':'Example University','title':'Faculty','url':'https://example.edu/1'})
    u=list_universities(s)[0]
    s.save_settings({'saved_search_enabled':True,'search_targets':[target('university',u['id'])]})
    with pytest.raises(ValueError,match='Confirm'):build_plan(s,s.settings(),'both')
    update_university(s,u['id'],{'career_url':'https://example.edu/careers','career_verified':True})
    _,urls,sel=build_plan(s,s.settings(),'both');assert urls[0][1]=='https://example.edu/careers' and len(urls)==1
    assert selection_reasons({'institution':'Other University'},sel)
    assert not selection_reasons({'institution':'Example University'},sel)

def test_custom_url_only_is_scoped(tmp_path):
    s=Store(tmp_path);s.save_settings({'saved_search_enabled':True,'search_targets':[target('url','https://example.edu/careers')]})
    _,urls,_=build_plan(s,s.settings(),'both');assert len(urls)==1 and urls[0][1]=='https://example.edu/careers'

def test_unchecked_saved_dimensions_do_not_reuse_old_constraints(tmp_path):
    s=Store(tmp_path);s.save_settings({'regions':['CA'],'countries':['United States'],'queries':['old query'],'saved_search_enabled':True,'search_targets':[target('board','LinkedIn'),target('job','old query',False),target('state','CA',False),target('country','United States',False)]})
    eff,urls,sel=build_plan(s,s.settings(),'both')
    assert eff['queries']==[] and eff['regions']==[] and eff['countries']==[]
    assert 'old+query' not in urls[0][1] and 'CA' not in urls[0][1]

def test_search_execution_applies_saved_job_and_state_filters(tmp_path,monkeypatch):
    import json
    from coach.services import Search
    s=Store(tmp_path);s.save_settings({'network_enabled':True,'max_pages':1,'saved_search_enabled':True,'search_targets':[target('board','https://example.edu/jobs'),target('job','robotics'),target('state','NY')]})
    records=[]
    for i,(title,region) in enumerate([('Robotics Faculty','CA'),('Python Faculty','NY'),('Robotics Faculty','NY')]):
        records.append({'@type':'JobPosting','title':title,'hiringOrganization':{'name':'Example University'},'description':title,'jobLocation':{'address':{'addressRegion':region,'addressCountry':'US'}},'url':'https://example.edu/job/'+str(i)})
    html='<script type="application/ld+json">'+json.dumps(records)+'</script>'+''.join('<a href="'+r['url']+'">Faculty job</a>' for r in records)
    monkeypatch.setattr('coach.services.fetch',lambda url,cfg:(html,url))
    result=Search(s).run('both',lambda text:None)
    assert result['added']==1 and result['filtered']==2
    assert s.jobs()[0]['region']=='NY' and s.jobs()[0]['title']=='Robotics Faculty'
