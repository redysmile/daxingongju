"""Password hashing, durable sessions and login throttling for the team workspace."""
import hashlib, hmac, os, secrets, time
from http.cookies import SimpleCookie

SESSION_TTL = 12 * 60 * 60

def enabled(): return os.environ.get('SCOUT_REQUIRE_AUTH', '0') == '1'
def secure_cookies(): return os.environ.get('SCOUT_COOKIE_SECURE', '1') != '0'

def init(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY, password_hash TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS login_attempts(key TEXT PRIMARY KEY, count INTEGER NOT NULL, window INTEGER NOT NULL);
    ''')

def hash_password(password):
    if not isinstance(password, str) or not 14 <= len(password) <= 1024:
        raise ValueError('密码长度应为 14–1024 个字符')
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ':' + digest

def verify(password, stored):
    try:
        salt, expected = stored.split(':')
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(expected, actual)
    except (ValueError, AttributeError, UnicodeError):
        return False

# Use the same expensive hash check for unknown usernames.
DUMMY_HASH = hash_password(secrets.token_urlsafe(32))

def set_user(db, username, password):
    if not isinstance(username, str) or not 1 <= len(username) <= 60 or not all(c.isalnum() or c in '_-.' for c in username):
        raise ValueError('用户名须为 1–60 个字母、数字或 _-.')
    hashed = hash_password(password)
    db.execute('INSERT INTO users VALUES(?,?) ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash', (username, hashed))
    db.execute('DELETE FROM sessions WHERE username=?', (username,))

def bootstrap(connect):
    if not enabled(): return
    with connect() as db:
        if db.execute('SELECT 1 FROM users LIMIT 1').fetchone(): return
        user = os.environ.get('SCOUT_ADMIN_USERNAME', 'admin')
        password = os.environ.get('SCOUT_ADMIN_PASSWORD', '')
        if not password:
            raise RuntimeError('首次启用登录须安全设置 SCOUT_ADMIN_PASSWORD（至少 14 字符），或先运行 manage.py user-add。')
        set_user(db, user, password)

def token_from(headers):
    try:
        cookies = SimpleCookie(); cookies.load(headers.get('Cookie', ''))
        token = cookies.get('scout_session')
        return token.value if token else ''
    except Exception:
        return ''

def token_hash(token): return hashlib.sha256(token.encode()).hexdigest()

def current_user(db, headers):
    token = token_from(headers)
    if not token or len(token) > 200: return None
    row = db.execute('SELECT s.username FROM sessions s JOIN users u ON u.username=s.username WHERE token_hash=? AND expires>?', (token_hash(token), int(time.time()))).fetchone()
    return row[0] if row else None

def cookie(token='', expire=False):
    value = f'scout_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={0 if expire else SESSION_TTL}'
    if secure_cookies(): value += '; Secure'
    return value

def login(db, username, password, address):
    if not isinstance(username,str) or not isinstance(password,str) or len(username)>60 or len(password)>1024:
        raise ValueError('用户名或密码错误')
    timestamp = int(time.time())
    db.execute('DELETE FROM sessions WHERE expires<=?', (timestamp,))
    db.execute('DELETE FROM login_attempts WHERE window<?', (timestamp-900,))
    keys=[('ip:'+address,40),('user:'+username,8)]
    for key,limit in keys:
        row=db.execute('SELECT count FROM login_attempts WHERE key=?',(key,)).fetchone()
        if row and row[0]>=limit: return 429, {'error':'登录尝试过多，请 15 分钟后再试'}, None
    for key,_ in keys:
        db.execute('INSERT INTO login_attempts VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1',(key,timestamp))
    row=db.execute('SELECT password_hash FROM users WHERE username=?',(username,)).fetchone()
    valid=verify(password,row[0] if row else DUMMY_HASH)
    if not row or not valid: return 401, {'error':'用户名或密码错误'}, None
    db.execute('DELETE FROM login_attempts WHERE key=?',('user:'+username,))
    token=secrets.token_urlsafe(32)
    db.execute('INSERT INTO sessions VALUES(?,?,?)',(token_hash(token),username,timestamp+SESSION_TTL))
    return 200, {'ok':True,'username':username}, cookie(token)

def logout(db, headers):
    token=token_from(headers)
    if token: db.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash(token),))
