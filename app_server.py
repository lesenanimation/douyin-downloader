import os, sys, re, json, random, time, queue, sqlite3, subprocess, threading
from pathlib import Path

import yaml
from flask import Flask, request, jsonify, Response, send_from_directory

BASE_DIR = Path(__file__).parent

# 独立化（脱离工具区 desktop_port_registry 共享模块）：常量本地写死。
APP_KEY = 'douyin-downloader'
APP_ID = 'XD2.DouyinFavDL.Client.1'
APP_NAME = '抖音收藏下载器'
SERVER_HOST = '127.0.0.1'
DEFAULT_PORT = 5091

CONFIG_PATH = BASE_DIR / 'config.yml'
PYTHON_EXE = sys.executable
ANSI_RE = re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
_DOWNLOAD_ERROR_RE = re.compile(r'Download error for (.+?):')
_RETRY_RE = re.compile(r'retrying in \d+s', re.IGNORECASE)
# 进度解析：作品总数 / 当前文件 / 已传字节
_TOTAL_RE = re.compile(r'(?:共|total of|found)\s*(\d+)\s*(?:个作品|works|videos|items)?', re.IGNORECASE)
_CURFILE_RE = re.compile(r'(?:正在下载|downloading|下载)[::\s]+(.+?\.(?:mp4|jpg|jpeg|png|webp|mp3|m4a))', re.IGNORECASE)
_SIZE_RE = re.compile(r'([\d.]+)\s*(KB|MB|GB)\b', re.IGNORECASE)
_UNIT = {'kb': 1024, 'mb': 1024 ** 2, 'gb': 1024 ** 3}

app = Flask(__name__, static_folder=str(BASE_DIR / 'web'), static_url_path='')

_state = {
    'status': 'idle',
    'process': None,
    'logs': [],
    'stats': {'total': 0, 'completed': 0, 'failed': 0, 'skipped': 0},
    'progress': {'total': 0, 'done': 0, 'percent': 0, 'current_file': '',
                 'bytes': 0, 'started_at': 0, 'elapsed': 0, 'speed': 0, 'eta': 0},
}


def _reset_progress():
    with _lock:
        _state['progress'] = {'total': 0, 'done': 0, 'percent': 0, 'current_file': '',
                              'bytes': 0, 'started_at': time.time(), 'elapsed': 0,
                              'speed': 0, 'eta': 0}


def _parse_progress(line):
    """从日志行提取进度信息，返回是否有变化。"""
    changed = False
    p = _state['progress']

    m = _TOTAL_RE.search(line)
    if m:
        try:
            n = int(m.group(1))
            if n > 0 and n != p['total']:
                p['total'] = n
                changed = True
        except ValueError:
            pass

    m = _CURFILE_RE.search(line)
    if m:
        name = m.group(1).strip().strip('"\'')
        name = name.split('/')[-1].split('\\')[-1]
        if name and name != p['current_file']:
            p['current_file'] = name
            changed = True

    m = _SIZE_RE.search(line)
    if m:
        try:
            p['bytes'] += float(m.group(1)) * _UNIT[m.group(2).lower()]
            changed = True
        except (ValueError, KeyError):
            pass

    return changed


def _push_stats():
    """同时广播 stats 与派生进度。"""
    with _lock:
        stats = dict(_state['stats'])
        prog = _recalc_progress()
    _broadcast('stats', stats)
    _broadcast('progress', prog)


def _recalc_progress():
    """基于 stats 与耗时刷新派生字段。"""
    p = _state['progress']
    s = _state['stats']
    p['done'] = s['completed'] + s['skipped'] + s['failed']
    if p['total'] > 0:
        p['percent'] = min(100, round(p['done'] / p['total'] * 100))
    elif p['done'] > 0:
        p['percent'] = 0
    started = p['started_at'] or 0
    p['elapsed'] = round(time.time() - started, 1) if started else 0
    if p['elapsed'] > 0 and p['bytes'] > 0:
        p['speed'] = p['bytes'] / p['elapsed']
    if p['done'] > 0 and p['total'] > p['done'] and p['elapsed'] > 0:
        per = p['elapsed'] / p['done']
        p['eta'] = int(per * (p['total'] - p['done']))
    else:
        p['eta'] = 0
    return dict(p)
_sse_queues = []
_lock = threading.Lock()
_sse_lock = threading.Lock()

_monitor = {
    'active': False,
    'interval': 3600,
    'last_check': None,
    'next_check': None,
    'session_count': 0,
}
_monitor_download_done = threading.Event()


def _broadcast(event, data):
    with _sse_lock:
        dead = []
        for q in _sse_queues:
            try:
                q.put_nowait({'event': event, 'data': data})
            except queue.Full:
                dead.append(q)
        for q in dead:
            _sse_queues.remove(q)


def _add_log(line):
    line = ANSI_RE.sub('', line).strip()
    if not line:
        return
    ts = time.strftime('%H:%M:%S')
    entry = {'time': ts, 'text': line}
    prog = None
    with _lock:
        _state['logs'].append(entry)
        if len(_state['logs']) > 500:
            _state['logs'] = _state['logs'][-300:]
        if _state['status'] == 'downloading' and _parse_progress(line):
            prog = _recalc_progress()
    _broadcast('log', entry)
    if prog is not None:
        _broadcast('progress', prog)


def _sub_env():
    env = os.environ.copy()
    env['PYTHONUNBUFFERED'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


def _sub_kwargs():
    kw = {'env': _sub_env(), 'cwd': str(BASE_DIR),
           'text': True, 'encoding': 'utf-8', 'errors': 'replace'}
    if sys.platform == 'win32':
        kw['creationflags'] = subprocess.CREATE_NO_WINDOW
    return kw


def _load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    return {}


def _save_config(cfg):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


# ── Routes ──

@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/favicon.ico')
def favicon():
    ico = BASE_DIR / 'icon.ico'
    if ico.exists():
        return send_from_directory(str(BASE_DIR), 'icon.ico', mimetype='image/x-icon')
    return '', 204


@app.route('/api/config', methods=['GET'])
def get_config():
    cfg = _load_config()
    safe = {k: v for k, v in cfg.items() if k != 'cookies'}
    return jsonify(safe)


@app.route('/api/config', methods=['POST'])
def update_config():
    updates = request.json or {}
    cfg = _load_config()
    for key in ('path', 'thread', 'rate_limit', 'cover', 'json', 'music', 'folderstyle'):
        if key in updates:
            cfg[key] = updates[key]
    if 'mode' in updates:
        new_modes = updates['mode'] if isinstance(updates['mode'], list) else [updates['mode']]
        cfg['mode'] = new_modes
        _auto_fix_link(cfg, new_modes)
    if 'number' in updates:
        cfg.setdefault('number', {}).update(updates['number'])
    if 'monitor_interval' in updates:
        cfg.setdefault('monitor', {})['interval'] = int(updates['monitor_interval'])
    _save_config(cfg)
    return jsonify({'ok': True})


_SELF_LINK = 'https://www.douyin.com/user/self?showTab=favorite_collection'
_SEC_UID_RE = re.compile(r'/user/([A-Za-z0-9_-]+)')


def _auto_fix_link(cfg, modes):
    """Ensure link matches the mode: collect/collectmix need /user/self, others need /user/<sec_uid>."""
    is_collect = any(m in ('collect', 'collectmix') for m in modes)
    links = cfg.get('link', [])
    if not isinstance(links, list):
        links = [links] if links else []

    current_sec_uid = None
    for lnk in links:
        m = _SEC_UID_RE.search(str(lnk))
        if m and m.group(1) != 'self':
            current_sec_uid = m.group(1)
            break

    if is_collect:
        cfg['link'] = [_SELF_LINK]
    elif current_sec_uid:
        cfg['link'] = [f'https://www.douyin.com/user/{current_sec_uid}']


@app.route('/api/cookie/status')
def cookie_status():
    cfg = _load_config()
    cookies = cfg.get('cookies', {})
    has = bool(cookies.get('ttwid') or cookies.get('odin_tt'))
    return jsonify({'valid': has})


@app.route('/api/cookie/fetch', methods=['POST'])
def start_cookie_fetch():
    with _lock:
        if _state['status'] != 'idle':
            return jsonify({'error': '有任务正在运行，请先停止'}), 400
        _state['status'] = 'fetching_cookie'

    _add_log('正在启动浏览器，请在打开的浏览器中扫码登录抖音...')
    _broadcast('status', {'status': 'fetching_cookie'})

    def run():
        try:
            proc = subprocess.Popen(
                [PYTHON_EXE, '-m', 'tools.cookie_fetcher', '--config', str(CONFIG_PATH)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                **_sub_kwargs())
            with _lock:
                _state['process'] = proc
            for line in iter(proc.stdout.readline, ''):
                _add_log(line)
            proc.wait()
            _add_log(f'Cookie 获取流程结束 (code {proc.returncode})')
        except Exception as e:
            _add_log(f'Cookie 获取出错: {e}')
        finally:
            with _lock:
                _state['status'] = 'idle'
                _state['process'] = None
            _broadcast('status', {'status': 'idle'})
            _broadcast('cookie', {'valid': _load_config().get('cookies', {}).get('ttwid', '') != ''})

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'ok': True})


@app.route('/api/cookie/confirm', methods=['POST'])
def confirm_cookie():
    with _lock:
        proc = _state.get('process')
    if proc and proc.stdin and proc.poll() is None:
        try:
            proc.stdin.write('\n')
            proc.stdin.flush()
            _add_log('已确认登录，正在提取 Cookie...')
            return jsonify({'ok': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 400
    return jsonify({'error': '没有正在运行的 Cookie 获取进程'}), 400


def _start_download_task(auto=False):
    """Shared download logic. Returns (ok, error_msg)."""
    cfg = _load_config()
    cookies = cfg.get('cookies', {})
    if not (cookies.get('ttwid') or cookies.get('odin_tt')):
        return False, '请先获取 Cookie（点击上方"获取 Cookie"按钮）'

    tag = '[监听] ' if auto else ''

    with _lock:
        if _state['status'] != 'idle':
            return False, '有任务正在运行，请先停止'
        _state['status'] = 'downloading'
        _state['stats'] = {'total': 0, 'completed': 0, 'failed': 0, 'skipped': 0}
        if not auto:
            _state['logs'] = []
    _reset_progress()

    _add_log(f'{tag}开始下载...')
    _broadcast('status', {'status': 'downloading'})
    _broadcast('progress', _recalc_progress())

    def run():
        try:
            proc = subprocess.Popen(
                [PYTHON_EXE, 'run.py', '-c', str(CONFIG_PATH), '-v'],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                **_sub_kwargs())
            with _lock:
                _state['process'] = proc
            failed_items = []
            for line in iter(proc.stdout.readline, ''):
                _add_log(line)
                lo = line.lower()
                if any(k in lo for k in ('already downloaded', 'already exists locally', 'skipping')):
                    with _lock:
                        _state['stats']['skipped'] += 1
                    _push_stats()
                elif any(k in lo for k in ('downloaded', '下载完成', 'saved', '已保存')):
                    with _lock:
                        _state['stats']['completed'] += 1
                    _push_stats()
                elif ('error' in lo or '失败' in line) and not _RETRY_RE.search(line):
                    with _lock:
                        _state['stats']['failed'] += 1
                    _push_stats()
                    m = _DOWNLOAD_ERROR_RE.search(line)
                    if m:
                        failed_items.append(m.group(1).strip())
            proc.wait()
            rc = proc.returncode
            with _lock:
                stats = dict(_state['stats'])
            msg = f'{tag}下载任务结束 (code {rc})，新下载 {stats["completed"]} 个'
            if stats['skipped']:
                msg += f'，跳过已下载 {stats["skipped"]} 个'
            if stats['failed']:
                msg += f'，失败 {stats["failed"]} 个'
            _add_log(msg)
            if failed_items:
                seen = set()
                unique = []
                for name in failed_items:
                    if name not in seen:
                        seen.add(name)
                        unique.append(name)
                _add_log(f'{tag}失败项目 ({len(unique)} 个):')
                for name in unique:
                    _add_log(f'  ✗ {name}')
            _broadcast('done', stats)
        except Exception as e:
            _add_log(f'{tag}下载出错: {e}')
        finally:
            with _lock:
                _state['status'] = 'idle'
                _state['process'] = None
            _broadcast('status', {'status': 'idle'})
            _monitor_download_done.set()

    threading.Thread(target=run, daemon=True).start()
    return True, None


@app.route('/api/download/start', methods=['POST'])
def start_download():
    ok, err = _start_download_task(auto=False)
    if not ok:
        return jsonify({'error': err}), 400
    return jsonify({'ok': True})


@app.route('/api/download/stop', methods=['POST'])
def stop_download():
    with _lock:
        proc = _state.get('process')
    if proc and proc.poll() is None:
        proc.terminate()
        _add_log('已发送停止信号')
        return jsonify({'ok': True})
    return jsonify({'error': '没有正在运行的任务'}), 400


@app.route('/api/status')
def get_status():
    with _lock:
        return jsonify({
            'ok': True,
            'app_key': APP_KEY,
            'app_id': APP_ID,
            'app_name': APP_NAME,
            'port': DEFAULT_PORT,
            'status': _state['status'],
            'stats': _state['stats'],
            'progress': _recalc_progress(),
            'log_count': len(_state['logs']),
        })


@app.route('/api/logs')
def get_logs():
    with _lock:
        return jsonify({'logs': _state['logs'][-200:]})


@app.route('/api/events')
def sse_stream():
    q = queue.Queue(maxsize=200)
    with _sse_lock:
        _sse_queues.append(q)

    def gen():
        try:
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield f"event: {msg['event']}\ndata: {json.dumps(msg['data'], ensure_ascii=False)}\n\n"
                except queue.Empty:
                    yield ': keepalive\n\n'
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                if q in _sse_queues:
                    _sse_queues.remove(q)

    return Response(gen(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/download-dir/open', methods=['POST'])
def open_download_dir():
    cfg = _load_config()
    p = cfg.get('path', './Downloaded/')
    if not os.path.isabs(p):
        p = str(BASE_DIR / p)
    os.makedirs(p, exist_ok=True)
    if sys.platform == 'win32':
        os.startfile(p)
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', p])
    else:
        subprocess.Popen(['xdg-open', p])
    return jsonify({'ok': True})


@app.route('/api/download-dir/browse', methods=['POST'])
def browse_dir():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        cfg = _load_config()
        folder = filedialog.askdirectory(title='选择下载目录',
                                         initialdir=cfg.get('path', ''))
        root.destroy()
        return jsonify({'path': folder or ''})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


# ── Monitor ──

def _get_monitor_info():
    return {
        'active': _monitor['active'],
        'interval': _monitor['interval'],
        'last_check': _monitor['last_check'],
        'next_check': _monitor['next_check'],
        'session_count': _monitor['session_count'],
    }


def _monitor_loop():
    interval_min = _monitor['interval'] // 60
    _add_log(f'[监听] 自动监听已启动，约每 {interval_min} 分钟检查一次（含随机抖动）')

    while _monitor['active']:
        jitter = random.randint(0, max(1, _monitor['interval'] // 5))
        actual_wait = _monitor['interval'] + jitter
        _monitor['next_check'] = time.time() + actual_wait
        _broadcast('monitor', _get_monitor_info())

        target = _monitor['next_check']
        while _monitor['active'] and time.time() < target:
            time.sleep(1)

        if not _monitor['active']:
            break

        _monitor['last_check'] = time.time()
        _monitor['session_count'] += 1
        _add_log(f'[监听] 第 {_monitor["session_count"]} 轮自动检查...')

        _monitor_download_done.clear()
        ok, err = _start_download_task(auto=True)
        if ok:
            while _monitor['active'] and not _monitor_download_done.is_set():
                _monitor_download_done.wait(timeout=2)
        else:
            _add_log(f'[监听] 跳过本轮: {err}')

        _broadcast('monitor', _get_monitor_info())

    _monitor['next_check'] = None
    _add_log('[监听] 自动监听已停止')
    _broadcast('monitor', _get_monitor_info())


@app.route('/api/monitor/start', methods=['POST'])
def start_monitor():
    if _monitor['active']:
        return jsonify({'error': '监听已在运行'}), 400

    cfg = _load_config()
    monitor_cfg = cfg.get('monitor', {})
    interval_min = int(monitor_cfg.get('interval', 60) or 60)
    _monitor['interval'] = max(interval_min, 5) * 60
    _monitor['active'] = True
    _monitor['session_count'] = 0
    _monitor['last_check'] = None

    t = threading.Thread(target=_monitor_loop, daemon=True)
    t.start()

    return jsonify({'ok': True})


@app.route('/api/monitor/stop', methods=['POST'])
def stop_monitor():
    if not _monitor['active']:
        return jsonify({'error': '监听未在运行'}), 400
    _monitor['active'] = False
    _monitor_download_done.set()
    return jsonify({'ok': True})


@app.route('/api/monitor/status')
def monitor_status():
    return jsonify(_get_monitor_info())


# ── Failed Items DB ──

def _db_path():
    cfg = _load_config()
    return str(BASE_DIR / (cfg.get('database_path') or 'dy_downloader.db'))


def _query_failed(sql, params=()):
    try:
        conn = sqlite3.connect(_db_path())
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _exec_failed(sql, params=()):
    try:
        conn = sqlite3.connect(_db_path())
        conn.execute(sql, params)
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


@app.route('/api/failed-items')
def list_failed_items():
    rows = _query_failed(
        'SELECT aweme_id, title, author_name, error_reason, mode, failed_at, status '
        'FROM failed_downloads ORDER BY failed_at DESC'
    )
    return jsonify({'items': rows})


@app.route('/api/failed-items/<aweme_id>/ignore', methods=['POST'])
def ignore_failed_item(aweme_id):
    ok = _exec_failed(
        "UPDATE failed_downloads SET status = 'ignored' WHERE aweme_id = ?",
        (aweme_id,)
    )
    return jsonify({'ok': ok})


@app.route('/api/failed-items/<aweme_id>/unignore', methods=['POST'])
def unignore_failed_item(aweme_id):
    ok = _exec_failed(
        "UPDATE failed_downloads SET status = 'failed' WHERE aweme_id = ?",
        (aweme_id,)
    )
    return jsonify({'ok': ok})


@app.route('/api/failed-items/<aweme_id>', methods=['DELETE'])
def delete_failed_item(aweme_id):
    ok = _exec_failed(
        'DELETE FROM failed_downloads WHERE aweme_id = ?',
        (aweme_id,)
    )
    return jsonify({'ok': ok})


@app.route('/api/failed-items/ignore-all', methods=['POST'])
def ignore_all_failed():
    ok = _exec_failed(
        "UPDATE failed_downloads SET status = 'ignored' WHERE status = 'failed'"
    )
    return jsonify({'ok': ok})


@app.route('/api/failed-items/unignore-all', methods=['POST'])
def unignore_all_failed():
    ok = _exec_failed(
        "UPDATE failed_downloads SET status = 'failed' WHERE status = 'ignored'"
    )
    return jsonify({'ok': ok})


@app.route('/api/failed-items/clear', methods=['POST'])
def clear_failed_items():
    ok = _exec_failed('DELETE FROM failed_downloads')
    return jsonify({'ok': ok})


# ─── 仓库同步（独立化后内置）────────────────────────────────────────

import git_sync as _git_sync  # noqa: E402

_git_sync_lock = threading.Lock()
_git_sync_running = False


def _git_log_cb(level: str, msg: str) -> None:
    _broadcast('git', {'level': level, 'msg': msg, 'ts': time.time()})


def _run_git_sync_async(fetch_only: bool) -> None:
    global _git_sync_running
    try:
        _git_log_cb('info', f'=== 开始同步（fetch_only={fetch_only}）===')
        result = _git_sync.run_sync(fetch_only=fetch_only, log_callback=_git_log_cb)
        if result.get('success'):
            _git_log_cb('ok', '=== 同步成功 ===')
        else:
            _git_log_cb('err', '=== 同步失败 ===')
        # 推一份最新 status 给前端
        _broadcast('git_status', _git_sync.get_status())
    finally:
        with _git_sync_lock:
            _git_sync_running = False


@app.route('/api/git/status')
def api_git_status():
    return jsonify(_git_sync.get_status())


@app.route('/api/git/sync', methods=['POST'])
def api_git_sync():
    global _git_sync_running
    fetch_only = False
    if request.is_json:
        fetch_only = bool((request.get_json(silent=True) or {}).get('fetch_only'))
    elif request.args.get('fetch_only'):
        fetch_only = request.args.get('fetch_only') in ('1', 'true', 'yes')

    with _git_sync_lock:
        if _git_sync_running:
            return jsonify({'started': False, 'reason': 'already_running'}), 409
        _git_sync_running = True

    threading.Thread(
        target=_run_git_sync_async, args=(fetch_only,), daemon=True
    ).start()
    return jsonify({'started': True, 'fetch_only': fetch_only})


# ─── 文件合并查重 ────────────────────────────────────────────────

_merge_running = False
_merge_lock = threading.Lock()


@app.route("/api/merge/start", methods=["POST"])
def start_merge():
    global _merge_running
    with _merge_lock:
        if _merge_running:
            return jsonify({"started": False, "reason": "already_running"}), 409
        _merge_running = True

    data = request.get_json(silent=True) or {}
    sources = data.get("sources", [])
    output = data.get("output", "")
    mode = data.get("mode", "copy")
    extensions = data.get("extensions", None)
    recurse = data.get("recurse", True)
    clean_output = data.get("clean_output", False)

    def _merge_log_cb(event: str, info: dict) -> None:
        if event == "log":
            _broadcast("merge_log", {
                "msg": info.get("msg", ""),
                "level": info.get("level", "info"),
            })
        elif event == "progress":
            _broadcast("merge_progress", info)
        elif event == "done":
            _broadcast("merge_done", info)

    def _run():
        global _merge_running
        try:
            from core.file_merger import run_merge
            result = run_merge(
                sources=sources,
                output=output,
                mode=mode,
                extensions=extensions,
                recurse=recurse,
                clean_output=clean_output,
                progress_callback=_merge_log_cb,
            )
            if not result.get("success"):
                _broadcast("merge_log", {"msg": result.get("error", "未知错误"), "level": "error"})
            _broadcast("merge_done", result.get("stats", {}))
        except Exception as e:
            _broadcast("merge_log", {"msg": f"合并出错: {e}", "level": "error"})
        finally:
            with _merge_lock:
                _merge_running = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"started": True})


@app.route("/api/merge/status")
def merge_status():
    return jsonify({"running": _merge_running})


@app.route("/api/merge/config")
def merge_config():
    """读取原 file-merge-dedupe 工具的配置作为默认值。"""
    import json as _json
    cfg_path = Path(os.environ.get("LOCALAPPDATA", "")) / "MergeDedupe" / "config.json"
    defaults = {
        "sourceA": "",
        "sourceB": "",
        "output": "",
        "mode": "copy",
        "extensions": "",
        "recurse": True,
        "clean_output": False,
    }
    if cfg_path.exists():
        try:
            raw = _json.loads(cfg_path.read_text(encoding="utf-8"))
            defaults["sourceA"] = raw.get("SourceA", "")
            defaults["sourceB"] = raw.get("SourceB", "")
            defaults["output"] = raw.get("OutputPath", "")
            defaults["mode"] = (raw.get("Mode") or "copy").lower()
            defaults["extensions"] = raw.get("Extensions", "")
            defaults["recurse"] = bool(raw.get("Recurse", True))
            defaults["clean_output"] = bool(raw.get("CleanOutput", False))
        except Exception:
            pass
    # 兜底：用当前下载路径填充源和输出
    if not defaults["output"]:
        cfg = _load_config()
        defaults["output"] = cfg.get("path", "")
    return jsonify(defaults)


@app.route("/api/merge/config", methods=["POST"])
def save_merge_config():
    """保存合并配置，下次打开自动回填。"""
    import json as _json
    data = request.get_json(silent=True) or {}
    cfg_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "MergeDedupe"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "config.json"
    payload = {
        "SourceA": data.get("sourceA", ""),
        "SourceB": data.get("sourceB", ""),
        "OutputPath": data.get("output", ""),
        "Mode": data.get("mode", "copy").capitalize(),
        "Extensions": data.get("extensions", ""),
        "Recurse": data.get("recurse", True),
        "CleanOutput": data.get("clean_output", False),
        "DeleteDupes": True,
    }
    cfg_path.write_text(_json.dumps(payload, ensure_ascii=False, indent=4), encoding="utf-8")
    return jsonify({"ok": True})


def start_server(port=DEFAULT_PORT):
    cfg = _load_config()
    p = cfg.get('path', './Downloaded/')
    if not os.path.isabs(p):
        p = str(BASE_DIR / p)
    os.makedirs(p, exist_ok=True)
    app.run(host=SERVER_HOST, port=port, debug=False, threaded=True)


if __name__ == '__main__':
    start_server()
