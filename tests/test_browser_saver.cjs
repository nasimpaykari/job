const {JSDOM}=require('jsdom');const fs=require('node:fs');const path=require('node:path');const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../browser_saver'),source=fs.readFileSync(path.join(root,'extract.js'),'utf8');
function capture(html,selection='',url='https://example.edu/jobs/1'){const d=new JSDOM(html,{url,runScripts:'outside-only'});if(selection)d.window.getSelection=()=>({toString:()=>selection});d.window.eval(source);const result=JSON.parse(JSON.stringify(d.window.extractJobCapture()));d.window.close();return result;}
const job={"@type":"JobPosting",title:'Robotics Faculty',description:'<p>Teach robotics.</p><p>Submit a CV.</p>',hiringOrganization:{name:'Example University'},validThrough:'2027-01-15',jobLocation:{address:{addressLocality:'Newark',addressRegion:'NJ',addressCountry:'US'}}};
const page='<h1>Robotics Faculty</h1><script type="application/ld+json">'+JSON.stringify(job)+'</script>';
let c=capture(page);assert.equal(c.title,'Robotics Faculty');assert.equal(c.institution,'Example University');assert.equal(c.region,'NJ');assert.ok(c.description.includes('Teach robotics.\n'));assert.equal(c.method,'structured');
c=capture(page,'Selected job text');assert.equal(c.description,'Selected job text');assert.equal(c.title,'');assert.equal(c.institution,'');assert.equal(c.method,'selection');
c=capture('<h1>Jobs</h1><script type="application/ld+json">'+JSON.stringify([job,{...job,title:'Other role'}])+'</script><main>Choose a role</main>');assert.equal(c.institution,'');assert.ok(c.multiple_postings);
c=capture('<main><h1>Role</h1><p>Work here.</p><form>PRIVATE_FORM<input value="SECRET"></form><script>SECRET_SCRIPT</script><p hidden>HIDDEN</p></main>');assert.ok(!/PRIVATE_FORM|SECRET|HIDDEN/.test(c.description));
c=capture(page,'x'.repeat(60005));assert.equal(c.description.length,60000);assert.ok(c.truncated);
c=capture(page,'','https://example.edu/jobs/1?access_token=SECRET&jobId=123');assert.ok(!c.url.includes('SECRET'));assert.ok(c.url.includes('jobId=123'));assert.ok(c.removed_sensitive_query);
const manifest=JSON.parse(fs.readFileSync(path.join(root,'manifest.json')));assert.equal(manifest.manifest_version,3);assert.deepEqual(manifest.permissions.sort(),['activeTab','clipboardWrite','scripting','storage'].sort());assert.ok(!manifest.host_permissions&&!manifest.content_scripts&&!manifest.background);
console.log('Browser extraction tests passed: structured data, selection, ambiguity, form exclusion, truncation, sensitive URL parameters, and limited permissions.');
