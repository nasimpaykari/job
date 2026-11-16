"""Start a private local Job Coach. Never loads credentials from either old app."""
import argparse, json, webbrowser
from pathlib import Path
from coach.core import Store

def main():
    p=argparse.ArgumentParser(description='Unified Job Coach')
    p.add_argument('--data',default=str(Path(__file__).resolve().parent/'data'),help='Active data folder (choose before launch)')
    p.add_argument('--port',type=int,default=5050)
    p.add_argument('--import-db',help='Import either legacy database without modifying it')
    p.add_argument('--no-browser',action='store_true')
    p.add_argument('--no-scheduler',action='store_true',help='Run the web app without its background scheduler')
    p.add_argument('--scheduler-only',action='store_true',help='Run only the local background worker; configure schedules in the app first')
    p.add_argument('--host',default='127.0.0.1',help='Bind address; default is local-only')
    p.add_argument('--allowed-host',action='append',default=[],help='Explicit hostname/IP for a shared deployment; repeat as needed')
    args=p.parse_args();store=Store(args.data)
    if args.import_db:
        print(json.dumps(store.migrate(args.import_db),indent=2));return
    from coach.web import create_app
    if args.host!='127.0.0.1' and not args.allowed_host:p.error('For shared hosting, provide --allowed-host with the server hostname or IP and configure HTTPS before external access')
    app=create_app(args.data,allowed_hosts=args.allowed_host);url=f'http://127.0.0.1:{args.port}/?token='+app.extensions['launch_token']
    print('Open this private first-account setup URL (or sign in if already configured):\n'+url)
    print('Data folder:',Path(args.data).resolve())
    from coach.scheduler import Runner
    runner=Runner(args.data,app.extensions['scheduler_stores'])
    if not args.no_scheduler:
        running=runner.start()
        print('Scheduler running.' if running else 'Another scheduler already owns this data root.')
    if args.scheduler_only:
        if args.no_scheduler or not running:p.error('Scheduler-only requires an available worker lock and no --no-scheduler flag')
        try:
            while runner.thread.is_alive():runner.thread.join(1)
        except KeyboardInterrupt:runner.close();runner.thread.join()
        return
    if not args.no_browser:
        from threading import Timer
        Timer(1.0,lambda:webbrowser.open(url)).start()
    from werkzeug.serving import WSGIRequestHandler
    class PrivateRequestHandler(WSGIRequestHandler):
        def log_request(self,code='-',size='-'):
            if self.path.startswith('/api/mail/oauth/callback'):
                self.log('info','"GET /api/mail/oauth/callback [query redacted]" %s %s',code,size)
            else:super().log_request(code,size)
    try:app.run(host=args.host,port=args.port,debug=False,threaded=True,request_handler=PrivateRequestHandler)
    finally:runner.close()
if __name__=='__main__':main()
