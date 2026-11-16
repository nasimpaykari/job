"""Consistent, versioned workspace snapshots to laptop folders or rclone remotes."""
import json, re, shutil, sqlite3, subprocess, tempfile, zipfile
from pathlib import Path
from .core import uid,now

class Storage:
    def __init__(self,store):self.store=store
    def remotes(self):
        exe=shutil.which('rclone')
        if not exe:return {'available':False,'remotes':[],'message':'Install rclone, then run rclone config to authorize Google Drive or OneDrive.'}
        r=subprocess.run([exe,'listremotes'],capture_output=True,text=True,timeout=15)
        if r.returncode:raise ValueError('rclone could not list connections. Run rclone config in a terminal.')
        return {'available':True,'remotes':[x.rstrip(':') for x in r.stdout.splitlines() if x.strip()]}
    def snapshot(self,destination):
        with tempfile.TemporaryDirectory(prefix='coach-snapshot-') as tmp:
            tmp=Path(tmp);db=tmp/'coach.db'
            with self.store.lock,self.store.db() as c:
                dest=sqlite3.connect(db);c.backup(dest)
                row=dest.execute("SELECT value FROM kv WHERE key='settings'").fetchone()
                cfg=json.loads(row[0]);cfg['documents_root']=''
                dest.execute("UPDATE kv SET value=? WHERE key='settings'",(json.dumps(cfg),));dest.commit()
                # A restored backup is inert until its new owner opts in.
                if dest.execute("SELECT 1 FROM sqlite_master WHERE name='schedules'").fetchone():
                    dest.execute('UPDATE schedules SET enabled=0')
                    dest.execute("UPDATE schedule_runs SET status='interrupted' WHERE status IN ('running','queued','retry_wait','cancel_requested')")
                    pref=dest.execute("SELECT value FROM scheduler_meta WHERE key='prefs'").fetchone()
                    if pref:
                        prefs=json.loads(pref[0]);prefs.update(enabled=False,desktop=False)
                        dest.execute("UPDATE scheduler_meta SET value=? WHERE key='prefs'",(json.dumps(prefs),))
                    dest.execute("DELETE FROM scheduler_meta WHERE key='heartbeat'")
                    dest.execute("UPDATE reminder_inbox SET desktop='suppressed on restore'")
                    dest.commit()
                dest.close()
                with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as z:
                    z.write(db,'coach.db')
                    docs=self.store.documents_root()
                    if docs.exists():
                        for p in docs.rglob('*'):
                            if p.is_file() and not p.is_symlink():z.write(p,Path('documents')/p.relative_to(docs))
                    versions=self.store.root/'document_versions'
                    if versions.exists():
                        for p in versions.rglob('*'):
                            if p.is_file() and not p.is_symlink():z.write(p,Path('document_versions')/p.relative_to(versions))
                    packages=self.store.root/'application_packages'
                    if packages.exists():
                        for p in packages.glob('*.zip'):
                            if p.is_file() and not p.is_symlink():z.write(p,Path('application_packages')/p.name)
                    z.writestr('snapshot.json',json.dumps({'created':now(),'format':1,'excludes':['API keys','login accounts','other workspaces','email credentials, cache and drafts'],'restore':'Unzip into an empty data folder; launch main.py --data PATH and create a new owner account.'},indent=2))
        return destination
    def sync(self,log=lambda x:None):
        s=self.store.settings();cfg=s['storage'];mode=cfg['mode']
        if mode=='local':return {'message':'Workspace is stored locally. Choose a backup destination to create an additional copy.'}
        name='job-coach-'+now().replace(':','').replace('.','-')+'-'+uid()[:6]+'.zip'
        with tempfile.TemporaryDirectory(prefix='coach-sync-') as tmp:
            snap=self.snapshot(Path(tmp)/name);log('Created a consistent workspace snapshot; credentials excluded.')
            if mode=='folder':
                folder=Path(cfg['destination']).expanduser()
                if not folder.is_absolute():raise ValueError('Choose an absolute laptop or desktop-sync folder path')
                folder.mkdir(parents=True,exist_ok=True);shutil.copy2(snap,folder/name)
                result={'message':'Snapshot copied to '+str(folder/name),'name':name}
            elif mode in ['google_drive','onedrive']:
                if not s['network_enabled']:raise ValueError('Enable network access before cloud backup')
                target=cfg['destination']
                if not re.fullmatch(r'[A-Za-z0-9_-]+:[^\r\n]*',target):raise ValueError('Enter a configured rclone destination, for example mydrive:JobCoach')
                remote=target.split(':',1)[0]
                if remote not in self.remotes()['remotes']:raise ValueError('Authorize this remote with rclone config first')
                r=subprocess.run([shutil.which('rclone'),'copyto',str(snap),target.rstrip('/')+'/'+name,'--retries','2','--contimeout','15s','--timeout','60s'],capture_output=True,text=True,timeout=180)
                if r.returncode:raise ValueError('Cloud upload failed. Check rclone authorization and destination permissions; the working data is unchanged.')
                result={'message':'Snapshot uploaded to '+target,'name':name}
            else:raise ValueError('Unsupported storage mode')
        with self.store.db() as c:self.store.event(c,'','storage_sync',result)
        return result
