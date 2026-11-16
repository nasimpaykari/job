"""Named accounts, isolated candidate workspaces and collaborator notifications."""
import contextlib, json, re, sqlite3, threading, time
from pathlib import Path
from werkzeug.security import generate_password_hash, check_password_hash
from .core import uid, now

class Accounts:
    def __init__(self,root):
        self.root=Path(root).resolve();self.path=self.root/'accounts.db';self.lock=threading.RLock();self.attempts={}
        with self.db() as c:c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE COLLATE NOCASE,password TEXT,admin INTEGER,version INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS spaces(id TEXT PRIMARY KEY,owner TEXT,label TEXT,path TEXT);
        CREATE TABLE IF NOT EXISTS members(space TEXT,user TEXT,role TEXT,PRIMARY KEY(space,user));
        CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY,user TEXT,space TEXT,job_id TEXT,actor TEXT,message TEXT,created TEXT,seen INTEGER DEFAULT 0);
        ''')
    @contextlib.contextmanager
    def db(self):
        c=sqlite3.connect(self.path,timeout=30);c.row_factory=sqlite3.Row
        try:
            with c:yield c
        finally:c.close()
    def count(self):
        with self.db() as c:return c.execute('SELECT count(*) FROM users').fetchone()[0]
    @staticmethod
    def validate(username,password):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{3,40}',username):raise ValueError('Username: 3–40 letters, digits, dots, dashes or underscores')
        if len(password)<10:raise ValueError('Use a password of at least 10 characters')
    def create(self,username,password,first=False):
        self.validate(username,password)
        with self.lock,self.db() as c:
            if first and c.execute('SELECT 1 FROM users').fetchone():raise ValueError('Setup has already been completed')
            user=uid();space=uid();path='.' if first else 'workspaces/'+space
            try:c.execute('INSERT INTO users VALUES(?,?,?,?,1)',(user,username,generate_password_hash(password),int(first)))
            except sqlite3.IntegrityError:raise ValueError('Username is already taken') from None
            c.execute('INSERT INTO spaces VALUES(?,?,?,?)',(space,user,username+"’s workspace",path))
            c.execute('INSERT INTO members VALUES(?,?,?)',(space,user,'owner'))
        return self.user(user)
    def user(self,user):
        with self.db() as c:
            r=c.execute('SELECT id,username,admin,version FROM users WHERE id=?',(user,)).fetchone()
            return dict(r) if r else None
    def login(self,username,password,address='local'):
        key=(address,username.lower());stamp=time.monotonic()
        with self.lock:
            failures=[t for t in self.attempts.get(key,[]) if stamp-t<900]
            if len(failures)>=8:raise ValueError('Too many attempts. Try again in 15 minutes.')
            with self.db() as c:r=c.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
            valid=bool(r and check_password_hash(r['password'],password))
            if not valid:self.attempts[key]=failures+[stamp];raise ValueError('Incorrect username or password')
            self.attempts.pop(key,None)
        return self.user(r['id'])
    def change(self,user,current,username,password):
        with self.db() as c:r=c.execute('SELECT * FROM users WHERE id=?',(user,)).fetchone()
        if not r or not check_password_hash(r['password'],current):raise ValueError('Current password is incorrect')
        self.validate(username,password or current)
        with self.db() as c:
            try:c.execute('UPDATE users SET username=?,password=?,version=version+1 WHERE id=?',(username,generate_password_hash(password or current),user))
            except sqlite3.IntegrityError:raise ValueError('Username is already taken') from None
        return self.user(user)
    def spaces(self,user):
        with self.db() as c:return [dict(r) for r in c.execute('SELECT s.id,s.label,m.role FROM spaces s JOIN members m ON m.space=s.id WHERE m.user=?',(user,))]
    def access(self,space,user):
        with self.db() as c:
            r=c.execute('SELECT s.*,m.role FROM spaces s JOIN members m ON m.space=s.id WHERE s.id=? AND m.user=?',(space,user)).fetchone()
            if not r:return None
            result=dict(r);result['path']=str((self.root/result['path']).resolve());return result
    def members(self,space):
        with self.db() as c:return [dict(r) for r in c.execute('SELECT u.id,u.username,m.role FROM members m JOIN users u ON u.id=m.user WHERE m.space=?',(space,))]
    def share(self,space,actor,username,role):
        a=self.access(space,actor)
        if not a or a['role']!='owner':raise ValueError('Only the workspace owner can manage collaborators')
        if role not in ['viewer','editor','remove']:raise ValueError('Choose viewer, editor or remove')
        with self.db() as c:
            u=c.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()
            if not u:raise ValueError('No account with that exact username; ask the administrator to create it first')
            if u[0]==actor:raise ValueError('The owner cannot remove or downgrade their own access')
            if role=='remove':c.execute('DELETE FROM members WHERE space=? AND user=?',(space,u[0]))
            else:c.execute('INSERT INTO members VALUES(?,?,?) ON CONFLICT(space,user) DO UPDATE SET role=excluded.role',(space,u[0],role))
    def notify(self,space,actor,job_id,message):
        author=self.user(actor)
        if not author:return 0
        count=0
        with self.db() as c:
            for row in c.execute('SELECT user FROM members WHERE space=? AND user<>?',(space,actor)).fetchall():
                c.execute('INSERT INTO notifications VALUES(?,?,?,?,?,?,?,0)',(uid(),row[0],space,job_id,author['username'],message,now()));count+=1
        return count
    def inbox(self,user):
        with self.db() as c:return [dict(r) for r in c.execute('SELECT n.* FROM notifications n JOIN members m ON m.space=n.space AND m.user=n.user WHERE n.user=? ORDER BY n.created DESC LIMIT 100',(user,))]
    def seen(self,user,nid):
        with self.db() as c:c.execute('UPDATE notifications SET seen=1 WHERE id=? AND user=?',(nid,user))
