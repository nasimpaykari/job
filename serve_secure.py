"""Production WSGI entry behind a configured HTTPS reverse proxy. See README.md."""
import argparse,os
from pathlib import Path
from waitress import serve
from coach.web import create_app
from coach.scheduler import Runner
p=argparse.ArgumentParser();p.add_argument('--data',default=str(Path(__file__).parent/'data'));p.add_argument('--hostname',required=True);p.add_argument('--port',type=int,default=5050);a=p.parse_args()
os.environ['JOB_COACH_HTTPS']='1';app=create_app(a.data,allowed_hosts=[a.hostname]);runner=Runner(a.data,app.extensions['scheduler_stores']);runner.start()
print('First-owner setup token (keep private):',app.extensions['launch_token'])
try:serve(app,host='127.0.0.1',port=a.port,threads=8,trusted_proxy='127.0.0.1',trusted_proxy_headers={'x-forwarded-proto','x-forwarded-host'})
finally:runner.close()
