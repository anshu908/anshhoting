import os, sys, json, uuid, hashlib, zipfile, subprocess, threading
import shutil, platform, time, psutil, secrets, re, html
from datetime import datetime, timedelta
from collections import defaultdict
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, flash, abort, Response, make_response)

try:
    from flask_session import Session as FlaskSession
    _has_flask_session = True
except ImportError:
    _has_flask_session = False

try:
    from werkzeug.utils import secure_filename
except ImportError:
    def secure_filename(f): return re.sub(r'[^\w.\-]','_',f.replace('..',''))

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
# Persistent secret key — stored in secret.key file so sessions survive restarts
_key_file = os.path.join(BASE_DIR, 'secret.key')
if os.path.exists(_key_file):
    with open(_key_file) as _f: _sk = _f.read().strip()
else:
    _sk = secrets.token_hex(32)
    with open(_key_file, 'w') as _f: _f.write(_sk)
app.secret_key = _sk
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=12)

# ── Filesystem sessions (fixes Windows localhost cookie issues) ──────────────
_sess_dir = os.path.join(BASE_DIR, 'flask_sessions')
os.makedirs(_sess_dir, exist_ok=True)
if _has_flask_session:
    app.config['SESSION_TYPE']            = 'filesystem'
    app.config['SESSION_FILE_DIR']        = _sess_dir
    app.config['SESSION_PERMANENT']       = True
    app.config['SESSION_USE_SIGNER']      = True
    app.config['SESSION_FILE_THRESHOLD']  = 1000
    FlaskSession(app)
else:
    # Fallback: standard cookie sessions with safe config
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_NAME']     = 'ansh_session'
    app.config['SESSION_COOKIE_PATH']     = '/'
    app.config['SESSION_COOKIE_DOMAIN']   = None
    app.config['SESSION_COOKIE_SECURE']   = False
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
PROJ_DIR   = os.path.join(BASE_DIR, 'projects')
USERS_FILE = os.path.join(BASE_DIR, 'users.json')
PROJS_FILE = os.path.join(BASE_DIR, 'projects.json')

ALLOWED_EXT = {'py','js','zip','txt','json','html','css','ts','env','md',
               'yml','yaml','toml','cfg','ini','sh','jsx','tsx'}
MAX_LOG_LINES = 2000
PORT_START, PORT_END = 5001, 5500
IS_WIN = platform.system() == 'Windows'

ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = 'Admin@2024!'   # Strong default

# ── Rate limiting ─────────────────────────────────────
_login_attempts = defaultdict(list)   # ip -> [timestamps]
MAX_LOGIN_ATTEMPTS = 10
LOGIN_WINDOW = 300   # 5 minutes

# ── In-memory state ───────────────────────────────────
_processes  = {}
_logs       = {}
_start_ts   = {}
_dep_status = {}

PLAN_LIMITS = {
    'free':    {'storage_mb': 10,    'max_projects': 2,   'label': 'Free'},
    'starter': {'storage_mb': 100,   'max_projects': 5,   'label': 'Starter'},
    'pro':     {'storage_mb': 500,   'max_projects': 20,  'label': 'Pro'},
    'premium': {'storage_mb': 2000,  'max_projects': 100, 'label': 'Premium'},
    'admin':   {'storage_mb': 99999, 'max_projects': 999, 'label': 'Admin'},
}

# ════════════════════════════════════════════════════
# SECURITY HEADERS
# ════════════════════════════════════════════════════
@app.after_request
def add_security_headers(resp):
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['X-Frame-Options']        = 'DENY'
    resp.headers['X-XSS-Protection']       = '1; mode=block'
    resp.headers['Referrer-Policy']        = 'strict-origin-when-cross-origin'
    resp.headers['Permissions-Policy']     = 'geolocation=(), microphone=(), camera=()'
    # Remove server header
    resp.headers.pop('Server', None)
    return resp

# ════════════════════════════════════════════════════
# RUNTIME DETECTION
# ════════════════════════════════════════════════════
def find_python():
    cands = [sys.executable, 'python3', 'python']
    if IS_WIN:
        for v in ['312','311','310','39','38']:
            cands += [rf'C:\Python{v}\python.exe',
                      os.path.expandvars(rf'%LOCALAPPDATA%\Programs\Python\Python{v[:2]}\python.exe')]
    for c in cands:
        try:
            r = subprocess.run([c,'--version'], capture_output=True, timeout=5)
            if r.returncode == 0: return c
        except: continue
    return sys.executable

def find_exe(names):
    for n in names:
        try:
            if subprocess.run([n,'--version'], capture_output=True, timeout=5).returncode == 0: return n
        except: continue
    return None

PYTHON_BIN = find_python()
NODE_BIN   = find_exe(['node','node.exe'])
NPM_BIN    = find_exe(['npm','npm.cmd','npm.exe'])
GIT_BIN    = find_exe(['git','git.exe'])

# ════════════════════════════════════════════════════
# DB HELPERS
# ════════════════════════════════════════════════════
def _load(p, d=None):
    if not os.path.exists(p): return d if d is not None else {}
    try:
        with open(p,'r',encoding='utf-8') as f: return json.load(f)
    except: return d if d is not None else {}

def _save(p, data):
    # Atomic write
    tmp = p + '.tmp'
    with open(tmp,'w',encoding='utf-8') as f: json.dump(data, f, indent=2)
    os.replace(tmp, p)

load_users = lambda: _load(USERS_FILE)
save_users = lambda u: _save(USERS_FILE, u)
save_projs = lambda p: _save(PROJS_FILE, p)

def proj_path_for(pid):
    # Sanitize pid — only alphanumerics allowed
    if not re.match(r'^[a-f0-9]{10}$', pid): raise ValueError(f'Invalid pid: {pid}')
    return os.path.join(PROJ_DIR, pid)

def load_projs():
    data = _load(PROJS_FILE)
    changed = False
    for pid, p in data.items():
        try:
            correct = proj_path_for(pid)
        except ValueError:
            continue
        if p.get('path','').replace('\\','/') != correct.replace('\\','/'):
            p['path'] = correct; changed = True
    if changed: _save(PROJS_FILE, data)
    return data

hash_pw = lambda pw: hashlib.sha256((pw + 'anshhosting_salt_v4').encode()).hexdigest()
allowed = lambda fn: '.' in fn and fn.rsplit('.',1)[1].lower() in ALLOWED_EXT

def free_port():
    used = {p['port'] for p in load_projs().values() if 'port' in p}
    for p in range(PORT_START, PORT_END):
        if p not in used: return p
    return PORT_START

# ════════════════════════════════════════════════════
# PLAN HELPERS
# ════════════════════════════════════════════════════
def get_user_plan(uid):
    u = load_users().get(uid, {})
    if u.get('is_admin'): return 'admin'
    plan = u.get('plan','free')
    expiry = u.get('plan_expiry')
    if expiry and plan not in ('free','admin'):
        try:
            if datetime.fromisoformat(expiry) < datetime.now():
                users = load_users()
                users[uid]['plan'] = 'free'; users[uid]['plan_expiry'] = None
                save_users(users); return 'free'
        except: pass
    return plan

def get_user_disk_mb(uid):
    total = 0
    for pid2, p in load_projs().items():
        if p['owner'] == uid:
            rp = os.path.join(PROJ_DIR, pid2)
            if os.path.exists(rp):
                for dp, dirs, files in os.walk(rp):
                    dirs[:] = [d for d in dirs if d not in ['.venv','node_modules','.git']]
                    for fn in files:
                        try: total += os.path.getsize(os.path.join(dp,fn))
                        except: pass
    return round(total/1048576, 2)

def user_can_deploy(uid):
    plan   = get_user_plan(uid)
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS['free'])
    disk   = get_user_disk_mb(uid)
    pcount = sum(1 for p in load_projs().values() if p['owner']==uid)
    if disk >= limits['storage_mb']:
        return False, f'Storage limit ({limits["storage_mb"]}MB) reached.'
    if pcount >= limits['max_projects']:
        return False, f'Project limit ({limits["max_projects"]}) reached.'
    return True, ''

def plan_info(uid):
    plan   = get_user_plan(uid)
    u      = load_users().get(uid, {})
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS['free'])
    # Admin can set custom storage per user
    storage_mb = u.get('custom_storage_mb') or limits['storage_mb']
    return {
        'plan': plan, 'label': limits['label'],
        'storage_mb': int(storage_mb), 'max_projects': limits['max_projects'],
        'disk_used': get_user_disk_mb(uid),
        'proj_count': sum(1 for p in load_projs().values() if p['owner']==uid),
        'expiry': u.get('plan_expiry'),
        'custom_storage': bool(u.get('custom_storage_mb')),
    }

# ════════════════════════════════════════════════════
# RATE LIMITER
# ════════════════════════════════════════════════════
def is_rate_limited(ip):
    now = time.time()
    attempts = _login_attempts[ip]
    # Remove old
    _login_attempts[ip] = [t for t in attempts if now - t < LOGIN_WINDOW]
    return len(_login_attempts[ip]) >= MAX_LOGIN_ATTEMPTS

def record_attempt(ip):
    _login_attempts[ip].append(time.time())

def remaining_attempts(ip):
    now = time.time()
    recent = [t for t in _login_attempts[ip] if now - t < LOGIN_WINDOW]
    return max(0, MAX_LOGIN_ATTEMPTS - len(recent))

# ════════════════════════════════════════════════════
# INPUT VALIDATION
# ════════════════════════════════════════════════════
def is_safe_username(un):
    return bool(re.match(r'^[a-zA-Z0-9_\-]{3,32}$', un))

def is_safe_email(em):
    return bool(re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', em)) and len(em) <= 120

def sanitize_str(s, maxlen=100):
    return html.escape(str(s).strip())[:maxlen]

# ════════════════════════════════════════════════════
# AUTH DECORATORS
# ════════════════════════════════════════════════════
def login_required(f):
    @wraps(f)
    def d(*a,**k):
        if 'uid' not in session:
            # Return JSON for API/AJAX calls instead of redirect
            if request.path.startswith('/api/'):
                return jsonify(error='Not authenticated'), 401
            flash('Please login to continue.','error')
            return redirect(url_for('login'))
        return f(*a,**k)
    return d

def admin_required(f):
    @wraps(f)
    def d(*a,**k):
        if 'uid' not in session:
            flash('Please login as admin to access the admin panel.', 'error')
            return redirect(url_for('login'))
        if not session.get('is_admin'):
            flash('⛔ Admin access required. Login with admin credentials.', 'error')
            session.clear()
            return redirect(url_for('login'))
        return f(*a,**k)
    return d

# ════════════════════════════════════════════════════
# LOGGING
# ════════════════════════════════════════════════════
def log(pid, msg, lvl='INFO'):
    line = f'[{datetime.now().strftime("%H:%M:%S")}] [{lvl}] {msg}'
    if pid not in _logs: _logs[pid] = []
    _logs[pid].append(line)
    if len(_logs[pid]) > MAX_LOG_LINES: _logs[pid] = _logs[pid][-MAX_LOG_LINES:]

def _stream(pid, stream, lvl):
    try:
        for raw in iter(stream.readline, b''):
            txt = raw.decode('utf-8', errors='replace').rstrip()
            if txt: log(pid, txt, lvl)
    except: pass
    finally: stream.close()

# ════════════════════════════════════════════════════
# LANGUAGE DETECTION
# ════════════════════════════════════════════════════
def detect_lang(path):
    try: files = os.listdir(path)
    except: return 'unknown'
    if any(f.endswith('.py') for f in files): return 'python'
    if 'package.json' in files or any(f.endswith('.js') for f in files): return 'nodejs'
    return 'unknown'

def list_entry_files(path, lang):
    try: files = os.listdir(path)
    except: return []
    if lang == 'python': return sorted([f for f in files if f.endswith('.py')])
    if lang == 'nodejs': return sorted([f for f in files if f.endswith(('.js','.ts')) and not f.startswith('.')])
    return []

def auto_entry(path, lang, preferred=None):
    try: files = os.listdir(path)
    except: return None
    if preferred and preferred in files: return preferred
    if lang == 'python':
        for n in ['main.py','app.py','server.py','run.py','bot.py','index.py']:
            if n in files: return n
        py = [f for f in files if f.endswith('.py')]
        return py[0] if py else None
    if lang == 'nodejs':
        for n in ['index.js','app.js','server.js','main.js','bot.js']:
            if n in files: return n
        js = [f for f in files if f.endswith('.js')]
        return js[0] if js else None
    return None

# ════════════════════════════════════════════════════
# ════════════════════════════════════════════════════
# DEPENDENCY INSTALLER — Live streaming output to terminal
# ════════════════════════════════════════════════════
def _run_live(pid, cmd, cwd=None, timeout=600, env=None):
    """Run a subprocess and stream every output line LIVE to terminal."""
    try:
        kw = dict(
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,   # merge stderr into stdout
            cwd=cwd, bufsize=0,         # unbuffered
            env=env or os.environ.copy()
        )
        proc = subprocess.Popen(cmd, **kw)
        for raw in iter(proc.stdout.readline, b''):
            line = raw.decode('utf-8', errors='replace').rstrip('\r\n')
            if line.strip():
                log(pid, line, 'OUT')
        proc.wait(timeout=timeout)
        return proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill(); log(pid, '[SETUP] ⏰ Command timed out!', 'ERR'); return -1
    except Exception as e:
        log(pid, f'[SETUP] Command failed: {e}', 'ERR'); return -1

def _do_pip(pid, proj_path, extra_pkgs=None, req_file=None):
    venv_path = os.path.join(proj_path, '.venv')
    venv_ok   = False
    # ── Step 1: Create venv ──────────────────────────────────
    log(pid, '╔══ PYTHON SETUP ══════════════════════════════╗', 'INFO')
    log(pid, f'  Python: {PYTHON_BIN}', 'INFO')
    log(pid, f'  Project: {proj_path}', 'INFO')
    log(pid, '╚═══════════════════════════════════════════════╝', 'INFO')
    log(pid, '', 'INFO')
    log(pid, '📦 Step 1/3 — Creating virtual environment...', 'INFO')
    try:
        rc = _run_live(pid, [PYTHON_BIN, '-m', 'venv', venv_path], timeout=90)
        venv_ok = rc == 0 and os.path.exists(venv_path)
        if venv_ok:
            log(pid, '✅ Virtual environment created at .venv/', 'INFO')
        else:
            log(pid, '⚠  venv creation failed — will use system pip', 'WARN')
    except Exception as e:
        log(pid, f'⚠  venv error: {e} — using system pip', 'WARN')

    # Resolve pip binary
    pip = None
    if venv_ok:
        for cand in [
            os.path.join(venv_path, 'Scripts', 'pip.exe'),
            os.path.join(venv_path, 'Scripts', 'pip'),
            os.path.join(venv_path, 'bin',     'pip'),
        ]:
            if os.path.exists(cand): pip = cand; break
    base = [pip] if pip else [PYTHON_BIN, '-m', 'pip']
    log(pid, f'  pip: {base[0]}', 'INFO')
    log(pid, '', 'INFO')

    # ── Step 2: Upgrade pip ──────────────────────────────────
    log(pid, '📦 Step 2/3 — Upgrading pip...', 'INFO')
    _run_live(pid, base + ['install', '--upgrade', 'pip'], timeout=120)
    log(pid, '', 'INFO')

    # ── Step 3a: Install requirements.txt ────────────────────
    if req_file and os.path.exists(req_file):
        log(pid, '📦 Step 3/3 — Installing requirements.txt...', 'INFO')
        log(pid, f'  File: {req_file}', 'INFO')
        rc = _run_live(pid, base + ['install', '-r', req_file, '--no-cache-dir'], timeout=600)
        if rc == 0:
            log(pid, '✅ requirements.txt installed successfully!', 'INFO')
        else:
            log(pid, '❌ requirements.txt install had errors (see above)', 'ERR')
    else:
        if req_file:
            log(pid, 'ℹ  No requirements.txt found — skipping', 'INFO')

    # ── Step 3b: Install extra packages ──────────────────────
    if extra_pkgs:
        log(pid, f'📦 Installing: {" ".join(extra_pkgs)}', 'INFO')
        rc = _run_live(pid, base + ['install'] + extra_pkgs + ['--no-cache-dir'], timeout=600)
        if rc == 0:
            log(pid, f'✅ Installed: {" ".join(extra_pkgs)}', 'INFO')
        else:
            log(pid, f'❌ Install errors for: {" ".join(extra_pkgs)}', 'ERR')

def _do_npm(pid, proj_path, extra_pkgs=None):
    if not NPM_BIN:
        log(pid, '❌ Node.js / npm not found in PATH!', 'ERR')
        return
    log(pid, '╔══ NODE.JS SETUP ═════════════════════════════╗', 'INFO')
    log(pid, f'  npm: {NPM_BIN}', 'INFO')
    log(pid, '╚═══════════════════════════════════════════════╝', 'INFO')
    if os.path.exists(os.path.join(proj_path, 'package.json')):
        log(pid, '📦 Running npm install...', 'INFO')
        rc = _run_live(pid, [NPM_BIN, 'install'], cwd=proj_path, timeout=300)
        log(pid, '✅ npm install done!' if rc==0 else '❌ npm install had errors',
            'INFO' if rc==0 else 'ERR')
    if extra_pkgs:
        log(pid, f'📦 npm install {" ".join(extra_pkgs)}...', 'INFO')
        rc = _run_live(pid, [NPM_BIN, 'install'] + extra_pkgs, cwd=proj_path, timeout=300)
        log(pid, f'✅ npm: {" ".join(extra_pkgs)} installed!' if rc==0 else '❌ npm install errors',
            'INFO' if rc==0 else 'ERR')

def install_and_autostart(pid, proj_path, lang):
    _dep_status[pid] = 'installing'
    log(pid, f'[SETUP] Platform: {platform.system()} | Python: {PYTHON_BIN}', 'INFO')
    if lang == 'python':
        _do_pip(pid, proj_path, req_file=os.path.join(proj_path, 'requirements.txt'))
    elif lang == 'nodejs':
        _do_npm(pid, proj_path)
    _dep_status[pid] = 'done'
    log(pid, '', 'INFO')
    log(pid, '═══════════════════════════════════════════════', 'INFO')
    log(pid, '🚀  Auto-starting application...', 'INFO')
    log(pid, '═══════════════════════════════════════════════', 'INFO')
    projs = load_projs()
    p = projs.get(pid)
    if p and p.get('entry_file'):
        proc = start_proc(pid, proj_path, p['language'], p['entry_file'], p['port'])
        log(pid, f'✅ Started on port :{p["port"]}' if proc else '❌ Auto-start failed — click Start button',
            'INFO' if proc else 'ERR')
    else:
        log(pid, '⚠  No entry file set — open project settings to configure', 'WARN')

def install_manual_bg(pid, proj_path, lang, packages_str):
    pkgs = [p.strip() for p in packages_str.replace(',', ' ').split() if p.strip()]
    if not pkgs: return
    safe_pkgs = [p for p in pkgs if re.match(r'^[a-zA-Z0-9\-_\.\[\]>=<!\^~]+$', p)]
    bad = set(pkgs) - set(safe_pkgs)
    if bad: log(pid, f'[MANUAL] Skipped unsafe package names: {", ".join(bad)}', 'WARN')
    if not safe_pkgs: return
    log(pid, f'[MANUAL] pip install {" ".join(safe_pkgs)}', 'INFO')
    if lang == 'python':   _do_pip(pid, proj_path, extra_pkgs=safe_pkgs)
    elif lang == 'nodejs': _do_npm(pid, proj_path, extra_pkgs=safe_pkgs)
    else:                   _do_pip(pid, proj_path, extra_pkgs=safe_pkgs)
    log(pid, '[MANUAL] ✅ Install complete.', 'INFO')

# ════════════════════════════════════════════════════
# PROCESS MANAGER
# ════════════════════════════════════════════════════
def start_proc(pid, proj_path, lang, entry, port):
    if proc_status(pid) == 'running':
        log(pid, f'[PROC] Already running on port {port}', 'INFO')
        return _processes.get(pid)
    # Validate entry file path — must stay within proj_path
    entry_path = os.path.realpath(os.path.join(proj_path, entry))
    if not entry_path.startswith(os.path.realpath(proj_path)):
        log(pid, '[PROC] ❌ Entry file path escape detected!', 'ERROR'); return None
    env = os.environ.copy()
    env['PORT'] = str(port); env['HOST'] = '0.0.0.0'
    if lang == 'python':
        venv_py = (os.path.join(proj_path,'.venv','Scripts','python.exe') if IS_WIN
                   else os.path.join(proj_path,'.venv','bin','python'))
        py = venv_py if os.path.exists(venv_py) else PYTHON_BIN
        cmd = [py, entry]; log(pid, f'[PROC] Python: {py}', 'INFO')
    elif lang == 'nodejs':
        if not NODE_BIN: log(pid,'[PROC] Node.js not found!','ERROR'); return None
        cmd = [NODE_BIN, entry]
    else:
        log(pid, f'[PROC] Unknown lang: {lang}','ERROR'); return None
    log(pid, f'[PROC] CMD: {" ".join(cmd)}', 'INFO')
    try:
        kw = dict(cwd=proj_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        if IS_WIN: kw['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(cmd, **kw)
        _processes[pid] = proc; _start_ts[pid] = datetime.now()
        threading.Thread(target=_stream, args=(pid, proc.stdout, 'OUT'), daemon=True).start()
        threading.Thread(target=_stream, args=(pid, proc.stderr, 'ERR'), daemon=True).start()
        log(pid, f'[PROC] ✅ PID={proc.pid} PORT={port}', 'INFO')
        return proc
    except Exception as e: log(pid, f'[PROC] Failed: {e}', 'ERROR'); return None

def stop_proc(pid):
    proc = _processes.get(pid)
    if not proc: return
    try:
        parent = psutil.Process(proc.pid)
        for c in parent.children(recursive=True): c.kill()
        parent.kill()
    except:
        try: proc.kill()
        except: pass
    _processes.pop(pid,None); _start_ts.pop(pid,None)
    log(pid,'[PROC] Stopped.','INFO')

def proc_status(pid):
    proc = _processes.get(pid)
    if proc is None: return 'stopped'
    if proc.poll() is None: return 'running'
    _processes.pop(pid,None); return 'error'

def proc_memory(pid):
    proc = _processes.get(pid)
    if not proc: return 0
    try: return round(psutil.Process(proc.pid).memory_info().rss/1048576, 1)
    except: return 0

def proc_uptime(pid):
    ts = _start_ts.get(pid)
    if not ts: return '—'
    s = int((datetime.now()-ts).total_seconds())
    if s<60: return f'{s}s'
    if s<3600: return f'{s//60}m {s%60}s'
    return f'{s//3600}h {(s%3600)//60}m'

# ════════════════════════════════════════════════════
# ERROR PAGES
# ════════════════════════════════════════════════════

# ════════════════════════════════════════════════════
# TEMPLATE CONTEXT — inject pi + cpu/ram into every template
# ════════════════════════════════════════════════════
@app.context_processor
def inject_globals():
    uid = session.get('uid')
    pi = plan_info(uid) if uid else {
        'plan':'free','label':'Free','storage_mb':10,'max_projects':2,
        'disk_used':0,'proj_count':0,'expiry':None
    }
    try:
        cpu = psutil.cpu_percent(interval=0)
        ram = psutil.virtual_memory().percent
    except:
        cpu, ram = 0, 0
    return dict(pi=pi, cpu=cpu, ram_pct=ram)

@app.errorhandler(403)
def forbidden(e):
    return render_template('error.html', code=403, msg='Access Forbidden',
        desc='You do not have permission to access this page.'), 403

@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', code=404, msg='Page Not Found',
        desc='The page you are looking for does not exist.'), 404

@app.errorhandler(413)
def too_large(e):
    flash('File too large. Max 500MB.','error'); return redirect(url_for('deploy'))

@app.errorhandler(500)
def server_error(e):
    return render_template('error.html', code=500, msg='Server Error',
        desc='Something went wrong on our end.'), 500

# ════════════════════════════════════════════════════
# PUBLIC ROUTES
# ════════════════════════════════════════════════════
@app.route('/')
def home(): return render_template('home.html')

@app.route('/login', methods=['GET','POST'])
def login():
    if 'uid' in session: return redirect(url_for('dashboard'))
    if request.method == 'POST':
        ip = request.remote_addr
        if is_rate_limited(ip):
            flash('Too many login attempts. Try again in 5 minutes.','error')
            return render_template('login.html')
        un = request.form.get('username','').strip()[:50]
        pw = request.form.get('password','')
        for uid,u in load_users().items():
            if u['username']==un and u['password']==hash_pw(pw):
                session.clear()
                session.permanent = True
                session['uid']      = uid
                session['username'] = u['username']
                session['is_admin'] = bool(u.get('is_admin', False))
                session.modified = True
                # Clear attempts
                _login_attempts.pop(ip, None)
                return redirect(url_for('dashboard'))
        record_attempt(ip)
        rem = remaining_attempts(ip)
        flash(f'Invalid credentials. {rem} attempts remaining.','error')
    return render_template('login.html')

@app.route('/register', methods=['GET','POST'])
def register():
    if 'uid' in session: return redirect(url_for('dashboard'))
    if request.method == 'POST':
        un = request.form.get('username','').strip()
        em = request.form.get('email','').strip().lower()
        pw = request.form.get('password','')
        cf = request.form.get('confirm','')
        # Validation
        if not all([un,em,pw]): flash('All fields required.','error'); return render_template('register.html')
        if not is_safe_username(un): flash('Username: 3-32 chars, letters/digits/_ only.','error'); return render_template('register.html')
        if not is_safe_email(em): flash('Invalid email address.','error'); return render_template('register.html')
        if len(pw) < 6: flash('Password min 6 chars.','error'); return render_template('register.html')
        if pw != cf: flash('Passwords do not match.','error'); return render_template('register.html')
        users = load_users()
        for u in users.values():
            if u['username'].lower()==un.lower(): flash('Username already taken.','error'); return render_template('register.html')
            if u['email'].lower()==em: flash('Email already registered.','error'); return render_template('register.html')
        uid = str(uuid.uuid4()); is_admin = (len(users)==0)
        users[uid] = {'id':uid,'username':un,'email':em,'password':hash_pw(pw),
                      'is_admin':is_admin,'plan':'admin' if is_admin else 'free',
                      'plan_expiry':None,'created_at':datetime.now().isoformat()}
        save_users(users)
        flash('✅ Account created! Login now.','success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('home'))

# ════════════════════════════════════════════════════
# DASHBOARD
# ════════════════════════════════════════════════════
@app.route('/dashboard')
@login_required
def dashboard():
    projs = load_projs()
    mine  = {k:v for k,v in projs.items() if v['owner']==session['uid']}
    for pid,p in mine.items():
        p['status']=proc_status(pid); p['memory']=proc_memory(pid)
        p['uptime']=proc_uptime(pid); p['dep_status']=_dep_status.get(pid,'')
    running = {k:v for k,v in mine.items() if v['status']=='running'}
    stopped = {k:v for k,v in mine.items() if v['status']!='running'}
    cpu = psutil.cpu_percent(interval=0.3); ram = psutil.virtual_memory()
    pi = plan_info(session['uid'])
    return render_template('dashboard.html',
        running=running, stopped=stopped, all_projs=mine,
        cpu=cpu, ram_pct=ram.percent,
        ram_used=round(ram.used/1073741824,1), ram_total=round(ram.total/1073741824,1),
        pi=pi)

# ════════════════════════════════════════════════════
# DEPLOY
# ════════════════════════════════════════════════════
@app.route('/deploy', methods=['GET','POST'])
@login_required
def deploy():
    if request.method == 'POST':
        ok, err = user_can_deploy(session['uid'])
        if not ok: flash(f'⚠ {err}','error'); return render_template('upload.html')
        name = sanitize_str(request.form.get('project_name',''), 80)
        desc = sanitize_str(request.form.get('description',''), 200)
        if not name: flash('Project name required.','error'); return render_template('upload.html')
        if 'file' not in request.files or request.files['file'].filename=='':
            flash('No file selected.','error'); return render_template('upload.html')
        f = request.files['file']
        fname = secure_filename(f.filename)
        if not allowed(fname): flash('File type not allowed.','error'); return render_template('upload.html')
        pid = uuid.uuid4().hex[:10]
        proj_path = os.path.join(PROJ_DIR, pid)
        os.makedirs(proj_path, exist_ok=True)
        ext = fname.rsplit('.',1)[1].lower()
        if ext == 'zip':
            zpath = os.path.join(UPLOAD_DIR, f'{pid}.zip')
            f.save(zpath)
            with zipfile.ZipFile(zpath,'r') as zf:
                for m in zf.namelist():
                    t = os.path.realpath(os.path.join(proj_path, m))
                    if t.startswith(os.path.realpath(proj_path)): zf.extract(m, proj_path)
            items = os.listdir(proj_path)
            if len(items)==1 and os.path.isdir(os.path.join(proj_path, items[0])):
                inner = os.path.join(proj_path, items[0])
                for it in os.listdir(inner): shutil.move(os.path.join(inner,it), proj_path)
                shutil.rmtree(inner, ignore_errors=True)
        else:
            f.save(os.path.join(proj_path, fname))
        lang = detect_lang(proj_path); port = free_port()
        session['_deploy_tmp'] = {
            'pid':pid, 'name':name, 'desc':desc, 'lang':lang, 'port':port,
            'proj_path':proj_path, 'available_entries': list_entry_files(proj_path, lang)
        }
        return redirect(url_for('deploy_configure'))
    return render_template('upload.html', pi=plan_info(session['uid']))

@app.route('/deploy/configure', methods=['GET','POST'])
@login_required
def deploy_configure():
    tmp = session.get('_deploy_tmp')
    if not tmp: flash('No pending deployment.','error'); return redirect(url_for('deploy'))
    if request.method == 'POST':
        tmp = session.get('_deploy_tmp')
        pid=tmp['pid']; proj_path=tmp['proj_path']
        lang=tmp['lang']; name=tmp['name']; desc=tmp['desc']; port=tmp['port']
        chosen = secure_filename(request.form.get('entry_file','').strip())
        entry  = auto_entry(proj_path, lang, preferred=chosen) or chosen
        projs  = load_projs()
        projs[pid] = {'id':pid,'name':name,'owner':session['uid'],'language':lang,
            'entry_file':entry,'port':port,'path':proj_path,
            'created_at':datetime.now().isoformat(),'description':desc,'source':'upload'}
        save_projs(projs)
        _logs[pid] = []
        log(pid, f'╔══ DEPLOY: {name} ══╗', 'INFO')
        log(pid, f'  Language: {lang} | Entry: {entry} | Port: {port}', 'INFO')
        session.pop('_deploy_tmp', None)
        threading.Thread(target=install_and_autostart, args=(pid,proj_path,lang), daemon=True).start()
        flash(f'✅ "{name}" deploying!','success')
        return redirect(url_for('terminal', pid=pid))
    return render_template('deploy_configure.html', tmp=tmp, pi=plan_info(session['uid']))

# ════════════════════════════════════════════════════
# PROJECT ROUTES
# ════════════════════════════════════════════════════
def _get_proj(pid):
    # Validate pid first
    if not re.match(r'^[a-f0-9]{10}$', pid): return None
    p = load_projs().get(pid)
    if not p: return None
    if p['owner'] != session.get('uid') and not session.get('is_admin'): return None
    p['path'] = os.path.join(PROJ_DIR, pid)
    return p

@app.route('/project/<pid>')
@login_required
def project_view(pid):
    p = _get_proj(pid)
    if not p: flash('Project not found.','error'); return redirect(url_for('dashboard'))
    p['status']=proc_status(pid); p['memory']=proc_memory(pid); p['uptime']=proc_uptime(pid)
    files = []
    if os.path.exists(p['path']):
        for fn in sorted(os.listdir(p['path'])):
            fp = os.path.join(p['path'], fn)
            if os.path.isfile(fp):
                files.append({'name':fn,'size':round(os.path.getsize(fp)/1024,1),
                               'ext':fn.rsplit('.',1)[-1] if '.' in fn else ''})
    return render_template('project.html', p=p, files=files,
        available_entries=list_entry_files(p['path'], p['language']),
        pi=plan_info(session['uid']))

@app.route('/project/<pid>/terminal')
@login_required
def terminal(pid):
    p = _get_proj(pid)
    if not p: flash('Not found.','error'); return redirect(url_for('dashboard'))
    p['status'] = proc_status(pid)
    return render_template('terminal.html', p=p, pi=plan_info(session['uid']))

@app.route('/api/project/<pid>/start', methods=['POST'])
@login_required
def api_start(pid):
    p = _get_proj(pid)
    if not p: return jsonify(error='Not found'),404
    if proc_status(pid)=='running': return jsonify(status='already_running', message='Already running!')
    proc = start_proc(pid, p['path'], p['language'], p['entry_file'], p['port'])
    return jsonify(status='started' if proc else 'error', port=p['port'])

@app.route('/api/project/<pid>/stop', methods=['POST'])
@login_required
def api_stop(pid):
    p = _get_proj(pid)
    if not p: return jsonify(error='Not found'),404
    stop_proc(pid); return jsonify(status='stopped')

@app.route('/api/project/<pid>/restart', methods=['POST'])
@login_required
def api_restart(pid):
    p = _get_proj(pid)
    if not p: return jsonify(error='Not found'),404
    stop_proc(pid); time.sleep(0.5)
    proc = start_proc(pid, p['path'], p['language'], p['entry_file'], p['port'])
    return jsonify(status='restarted' if proc else 'error')

@app.route('/project/<pid>/delete', methods=['POST'])
@login_required
def delete_project(pid):
    p = _get_proj(pid)
    if not p: flash('Not found.','error'); return redirect(url_for('dashboard'))
    stop_proc(pid)
    real = os.path.join(PROJ_DIR, pid)
    if os.path.exists(real): shutil.rmtree(real, ignore_errors=True)
    projs = load_projs(); projs.pop(pid, None); save_projs(projs)
    _logs.pop(pid, None); _dep_status.pop(pid, None)
    flash(f'Project "{p["name"]}" deleted.','success')
    return redirect(url_for('dashboard'))

@app.route('/api/project/<pid>/status')
@login_required
def api_status(pid):
    return jsonify(status=proc_status(pid), memory=proc_memory(pid), uptime=proc_uptime(pid))

@app.route('/api/logs/<pid>')
@login_required
def api_logs(pid):
    p = _get_proj(pid)
    if not p: return jsonify(error='Not found'),404
    after = int(request.args.get('after',0))
    lines = _logs.get(pid,[])
    return jsonify(logs=lines[after:], total=len(lines),
        status=proc_status(pid), memory=proc_memory(pid), uptime=proc_uptime(pid),
        dep_status=_dep_status.get(pid,''))

@app.route('/api/project/<pid>/set_entry', methods=['POST'])
@login_required
def api_set_entry(pid):
    p = _get_proj(pid)
    if not p: return jsonify(error='Not found'),404
    entry = secure_filename(request.json.get('entry_file','').strip())
    if not entry: return jsonify(error='No entry file'),400
    projs = load_projs()
    if pid in projs: projs[pid]['entry_file'] = entry; save_projs(projs)
    return jsonify(status='ok', entry_file=entry)

@app.route('/api/project/<pid>/install', methods=['POST'])
@login_required
def api_install(pid):
    p = _get_proj(pid)
    if not p: return jsonify(error='Not found'),404
    packages = request.json.get('packages','').strip()[:500]
    threading.Thread(target=install_manual_bg, args=(pid,p['path'],p['language'],packages), daemon=True).start()
    return jsonify(status='installing')

@app.route('/api/project/<pid>/reinstall', methods=['POST'])
@login_required
def api_reinstall(pid):
    p = _get_proj(pid)
    if not p: return jsonify(error='Not found'),404
    threading.Thread(target=install_and_autostart, args=(pid,p['path'],p['language']), daemon=True).start()
    return jsonify(status='reinstalling')

@app.route('/api/project/<pid>/file/delete', methods=['POST'])
@login_required
def api_file_delete(pid):
    p = _get_proj(pid)
    if not p: return jsonify(error='Not found'),404
    filename = secure_filename((request.get_json() or {}).get('filename',''))
    if not filename: return jsonify(error='No filename'),400
    target = os.path.realpath(os.path.join(p['path'], filename))
    if not target.startswith(os.path.realpath(p['path'])): return jsonify(error='Invalid path'),403
    if os.path.exists(target): os.remove(target); return jsonify(status='deleted')
    return jsonify(error='Not found'),404

@app.route('/api/project/<pid>/file/upload', methods=['POST'])
@login_required
def api_file_upload(pid):
    p = _get_proj(pid)
    if not p: return jsonify(error='Not found'),404
    if 'file' not in request.files: return jsonify(error='No file'),400
    f = request.files['file']
    if f and allowed(f.filename):
        fname = secure_filename(f.filename)
        f.save(os.path.join(p['path'], fname))
        return jsonify(status='uploaded', filename=fname)
    return jsonify(error='Invalid file type'),400

# ════════════════════════════════════════════════════
# ADMIN PANEL
# ════════════════════════════════════════════════════
@app.route('/admin')
@admin_required
def admin():
    users = load_users(); projs = load_projs()
    for pid,p in projs.items():
        p['status']=proc_status(pid); p['memory']=proc_memory(pid); p['uptime']=proc_uptime(pid)
        p['owner_name']=users.get(p['owner'],{}).get('username','Unknown')
    cpu=psutil.cpu_percent(interval=0.5); ram=psutil.virtual_memory()
    disk=psutil.disk_usage('/'); net=psutil.net_io_counters()
    return render_template('admin.html', users=users, projs=projs,
        cpu=cpu, ram_pct=ram.percent, ram_used=round(ram.used/1073741824,2), ram_total=round(ram.total/1073741824,2),
        disk_pct=disk.percent, disk_used=round(disk.used/1073741824,1), disk_total=round(disk.total/1073741824,1),
        net_sent=round(net.bytes_sent/1048576,1), net_recv=round(net.bytes_recv/1048576,1),
        python_bin=PYTHON_BIN, node_bin=NODE_BIN or 'Not found',
        os_platform=platform.platform(), plan_limits=PLAN_LIMITS,
        admin_user=ADMIN_USERNAME, admin_pass=ADMIN_PASSWORD)

@app.route('/admin/delete_project/<pid>', methods=['POST'])
@admin_required
def admin_del_project(pid):
    projs = load_projs(); p = projs.get(pid)
    if p:
        stop_proc(pid); shutil.rmtree(os.path.join(PROJ_DIR,pid), ignore_errors=True)
        projs.pop(pid); save_projs(projs)
    flash('Project deleted.','success'); return redirect(url_for('admin'))

@app.route('/admin/delete_user/<uid>', methods=['POST'])
@admin_required
def admin_del_user(uid):
    if uid==session['uid']: flash('Cannot delete yourself.','error'); return redirect(url_for('admin'))
    users = load_users(); users.pop(uid, None); save_users(users)
    flash('User deleted.','success'); return redirect(url_for('admin'))

@app.route('/admin/grant_plan', methods=['POST'])
@admin_required
def admin_grant_plan():
    email       = request.form.get('email','').strip().lower()
    plan        = request.form.get('plan','free')
    duration    = request.form.get('duration','month')
    custom_mb   = request.form.get('custom_mb','').strip()
    custom_days = request.form.get('custom_days','').strip()

    # Custom storage override
    if custom_mb and custom_mb.isdigit():
        custom_storage = int(custom_mb)
    else:
        custom_storage = None

    if plan not in PLAN_LIMITS and plan != 'custom':
        flash('Invalid plan.','error'); return redirect(url_for('admin'))

    users = load_users()
    target_uid = next((uid for uid,u in users.items()
                       if u['email'].lower()==email or u['username'].lower()==email), None)
    if not target_uid:
        flash(f'User "{email}" not found.','error'); return redirect(url_for('admin'))

    dur_map = {'month':30,'3months':90,'6months':180,'year':365,'lifetime':36500}
    if custom_days and custom_days.isdigit():
        days = int(custom_days)
    else:
        days = dur_map.get(duration, 30)

    expiry = datetime.now() + timedelta(days=days)
    users[target_uid]['plan'] = 'premium' if plan=='custom' else plan
    users[target_uid]['plan_expiry'] = expiry.isoformat()
    if custom_storage:
        users[target_uid]['custom_storage_mb'] = custom_storage
    else:
        users[target_uid].pop('custom_storage_mb', None)

    save_users(users)
    uname = users[target_uid]['username']
    mb_note = f' ({custom_storage}MB storage)' if custom_storage else ''
    flash(f'✅ Granted {plan.upper()} to @{uname} for {days} days{mb_note} · Expires {expiry.strftime("%Y-%m-%d")}.',"success")
    return redirect(url_for('admin'))

@app.route('/admin/revoke_plan/<uid>', methods=['POST'])
@admin_required
def admin_revoke_plan(uid):
    users = load_users()
    if uid in users and not users[uid].get('is_admin'):
        users[uid]['plan']='free'; users[uid]['plan_expiry']=None
        save_users(users); flash('Plan revoked.','success')
    return redirect(url_for('admin'))

@app.route('/admin/toggle_admin/<uid>', methods=['POST'])
@admin_required
def admin_toggle_admin(uid):
    users = load_users()
    if uid in users and uid!=session['uid']:
        users[uid]['is_admin'] = not users[uid].get('is_admin',False)
        save_users(users); flash('Admin status toggled.','success')
    return redirect(url_for('admin'))

@app.route('/admin/fix-paths', methods=['POST'])
@admin_required
def admin_fix_paths():
    projs = _load(PROJS_FILE); fixed = 0
    for pid,p in projs.items():
        correct = os.path.join(PROJ_DIR, pid)
        if p.get('path') != correct: p['path']=correct; fixed+=1
    if fixed: _save(PROJS_FILE, projs)
    flash(f'Fixed {fixed} paths.','success'); return redirect(url_for('admin'))

@app.route('/api/server/stats')
@login_required
def api_server_stats():
    cpu=psutil.cpu_percent(interval=0.3); ram=psutil.virtual_memory(); disk=psutil.disk_usage('/')
    return jsonify(cpu=cpu, ram_pct=ram.percent,
        ram_used=round(ram.used/1073741824,2), ram_total=round(ram.total/1073741824,2),
        disk_pct=disk.percent, disk_used=round(disk.used/1073741824,1), disk_total=round(disk.total/1073741824,1))

# ════════════════════════════════════════════════════
# ENTRYPOINT
# ════════════════════════════════════════════════════
def _seed_admin():
    users = load_users()
    for u in users.values():
        if u.get('is_admin'): return
    uid = str(uuid.uuid4())
    users[uid] = {'id':uid,'username':ADMIN_USERNAME,'email':'admin@anshhosting.local',
                  'password':hash_pw(ADMIN_PASSWORD),'is_admin':True,'plan':'admin',
                  'plan_expiry':None,'created_at':datetime.now().isoformat()}
    save_users(users)
    print(f'  ✓ Admin created: {ADMIN_USERNAME} / {ADMIN_PASSWORD}')

if __name__ == '__main__':
    for d in [UPLOAD_DIR, PROJ_DIR]:
        os.makedirs(d, exist_ok=True)
    for fp in [USERS_FILE, PROJS_FILE]:
        if not os.path.exists(fp): _save(fp, {})
    _seed_admin()
    print('╔═══════════════════════════════════════════╗')
    print('║    ⚡  ANSHHOSTING v4.1 — SECURE           ║')
    print('╠═══════════════════════════════════════════╣')
    print(f'║  URL     → http://0.0.0.0:5000            ║')
    print(f'║  Admin   → {ADMIN_USERNAME} / {ADMIN_PASSWORD:<28}║')
    print(f'║  Python  → {PYTHON_BIN[:29]:<29}║')
    print(f'║  Node.js → {(NODE_BIN or "NOT FOUND")[:29]:<29}║')
    print('╠═══════════════════════════════════════════╣')
    print('║  Security: Headers ✓ Rate-limit ✓          ║')
    print('║  Path validation ✓ XSS protect ✓          ║')
    print('╚═══════════════════════════════════════════╝')
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
