import sys, os, time, threading, traceback

IS_WIN = sys.platform == 'win32'
if IS_WIN:
    import ctypes

FROZEN = getattr(sys, 'frozen', False)
if FROZEN:
    EXE_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = getattr(sys, '_MEIPASS', EXE_DIR)
    os.chdir(EXE_DIR)
    sys.path.insert(0, EXE_DIR)
    if BUNDLE_DIR != EXE_DIR:
        sys.path.insert(1, BUNDLE_DIR)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    EXE_DIR = SCRIPT_DIR
    os.chdir(SCRIPT_DIR)
    sys.path.insert(0, SCRIPT_DIR)

# 独立化（脱离工具区 desktop_port_registry 共享模块）：
# 端口/元数据本地写死，wait_for_app_server 内联实现。
APP_KEY = 'douyin-downloader'
APP_ID = 'XD2.DouyinFavDL.Client.1'
APP_NAME = '抖音收藏下载器'
HOST = '127.0.0.1'
PORT = 5091
URL = f'http://{HOST}:{PORT}'
LOG_PATH = os.path.join(EXE_DIR, '_client.log')


def wait_for_app_server(*, host, port, expected_app_key, timeout=15.0,
                        status_path='/api/status'):
    """轮询 /api/status，确认是本应用而不是别的服务占用了端口。"""
    import json as _json
    import urllib.request as _urlreq
    deadline = time.time() + timeout
    last_error = f'timeout waiting for http://{host}:{port}{status_path}'
    while time.time() < deadline:
        try:
            with _urlreq.urlopen(f'http://{host}:{port}{status_path}', timeout=2.0) as resp:
                charset = resp.headers.get_content_charset() or 'utf-8'
                payload = _json.loads(resp.read().decode(charset))
            if isinstance(payload, dict) and payload.get('app_key') == expected_app_key:
                return True, payload, ''
            actual = ''
            if isinstance(payload, dict):
                actual = str(payload.get('app_key') or payload.get('app_id') or '')
            actual = actual or 'unknown-service'
            return False, payload if isinstance(payload, dict) else None, \
                f'port {port} responded with {actual}, expected {expected_app_key}'
        except Exception as exc:
            last_error = str(exc)
            time.sleep(0.3)
    return False, None, last_error


if FROZEN:
    try:
        _log_file = open(LOG_PATH, 'a', encoding='utf-8')
        sys.stdout = _log_file
        sys.stderr = _log_file
    except Exception:
        pass
if IS_WIN:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def log(msg):
    try:
        print(msg, flush=True)
    except Exception:
        pass
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(msg + '\n')
    except Exception:
        pass


if IS_WIN:
    class MARGINS(ctypes.Structure):
        _fields_ = [("cxLeftWidth", ctypes.c_int), ("cxRightWidth", ctypes.c_int),
                     ("cyTopHeight", ctypes.c_int), ("cyBottomHeight", ctypes.c_int)]


def start_flask():
    try:
        import app_server
        app_server.start_server(port=PORT)
    except Exception as e:
        log(f'[Flask Error] {e}')
        log(traceback.format_exc())


def wait_for_server(timeout=15):
    ok, _, detail = wait_for_app_server(
        host=HOST,
        port=PORT,
        expected_app_key=APP_KEY,
        timeout=timeout,
    )
    if ok:
        log('[OK] Flask server ready')
        return True
    log('[WARN] Flask server not responding correctly: ' + detail)
    return False


def main():
    log(f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] Starting DouyinFavDL client...')

    server_thread = threading.Thread(target=start_flask, daemon=True)
    server_thread.start()
    wait_for_server()

    try:
        from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                                     QFileDialog, QSystemTrayIcon, QMenu)
        from PyQt6.QtCore import Qt, QUrl, QObject, pyqtSlot, QSettings
        from PyQt6.QtGui import QIcon, QAction
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        from PyQt6.QtWebChannel import QWebChannel
        HAS_QT = True
    except ImportError:
        HAS_QT = False
        log('[WARN] PyQt6 not available, falling back to browser mode')

    if not HAS_QT:
        import webbrowser
        webbrowser.open(URL)
        log(f'[OK] Opened {URL} in default browser. Press Ctrl+C to stop.')
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return

    class Bridge(QObject):
        def __init__(self, window):
            super().__init__()
            self._w = window

        @pyqtSlot()
        def minimize(self):
            self._w.showMinimized()

        @pyqtSlot()
        def toggleMaximize(self):
            if self._w.isMaximized():
                self._w.showNormal()
            else:
                self._w.showMaximized()

        @pyqtSlot()
        def closeWindow(self):
            self._w.close()

        @pyqtSlot()
        def startDrag(self):
            wh = self._w.windowHandle()
            if wh:
                wh.startSystemMove()

        @pyqtSlot(result=str)
        def selectFolder(self):
            return QFileDialog.getExistingDirectory(self._w, '选择下载目录') or ''

    class MainWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle(APP_NAME)
            self.setMinimumSize(800, 560)

            # ── 窗口尺寸/位置记忆（手动保存，无边框窗口更可靠）──
            self._settings = QSettings('DouyinFavDL', 'Client')
            sx = self._settings.value('window/x')
            sy = self._settings.value('window/y')
            sw = self._settings.value('window/w')
            sh = self._settings.value('window/h')
            if sx is not None and sy is not None and sw is not None and sh is not None:
                self.setGeometry(int(sx), int(sy), int(sw), int(sh))
            else:
                self.resize(960, 680)

            self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
            self.setStyleSheet('QMainWindow { background: #1a1a2e; border-radius: 0px; }')

            icon_path = os.path.join(EXE_DIR, 'icon.ico')
            if os.path.isfile(icon_path):
                self.setWindowIcon(QIcon(icon_path))

            container = QWidget()
            lay = QVBoxLayout(container)
            lay.setContentsMargins(1, 1, 1, 1)

            self.webview = QWebEngineView()
            lay.addWidget(self.webview)
            self.setCentralWidget(container)

            self.channel = QWebChannel()
            self.bridge = Bridge(self)
            self.channel.registerObject('qtBridge', self.bridge)
            self.webview.page().setWebChannel(self.channel)
            self.webview.page().loadFinished.connect(self._on_load)
            self.webview.setUrl(QUrl(URL))
            self._resize_edge = 8  # 边缘拖拽调整大小

        def _on_load(self, ok):
            if ok:
                self.webview.page().runJavaScript('''
                (function() {
                    var s = document.createElement('script');
                    s.src = 'qrc:///qtwebchannel/qwebchannel.js';
                    s.onload = function() {
                        new QWebChannel(qt.webChannelTransport, function(ch) {
                            window.qtBridge = ch.objects.qtBridge;
                            var tb = document.getElementById('customTitlebar');
                            if (tb) {
                                tb.classList.add('visible');
                                tb.addEventListener('mousedown', function(e) {
                                    if (e.target.closest('.titlebar-btn') || e.target.closest('.titlebar-controls')) return;
                                    if (e.button === 0) window.qtBridge.startDrag();
                                });
                                tb.addEventListener('dblclick', function(e) {
                                    if (e.target.closest('.titlebar-btn')) return;
                                    window.qtBridge.toggleMaximize();
                                });
                            }
                        });
                    };
                    document.head.appendChild(s);
                })();
                ''')

        def showEvent(self, event):
            super().showEvent(event)
            if IS_WIN:
                self._enable_dwm_shadow()
                self._fix_taskbar_icon()

        def _enable_dwm_shadow(self):
            try:
                hwnd = int(self.winId())
                margins = MARGINS(1, 1, 1, 1)
                ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
            except Exception:
                pass

        def _fix_taskbar_icon(self):
            try:
                icon_path = os.path.join(EXE_DIR, 'icon.ico')
                if not os.path.isfile(icon_path):
                    return
                hwnd = int(self.winId())
                hicon = ctypes.windll.user32.LoadImageW(0, icon_path, 1, 0, 0, 0x00000010)
                if hicon:
                    ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 1, hicon)
                    ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 0, hicon)
            except Exception:
                pass

        def _edge_at(self, pos):
            r, e = self.rect(), self._resize_edge
            x, y = pos.x(), pos.y()
            edges = 0
            if x < e: edges |= 1
            if x > r.width() - e: edges |= 2
            if y < e: edges |= 4
            if y > r.height() - e: edges |= 8
            return edges

        def mousePressEvent(self, event):
            if event.button() == Qt.MouseButton.LeftButton:
                edges = self._edge_at(event.pos())
                if edges:
                    edge_map = {
                        1: Qt.Edge.LeftEdge, 2: Qt.Edge.RightEdge,
                        4: Qt.Edge.TopEdge, 8: Qt.Edge.BottomEdge,
                        5: Qt.Edge.LeftEdge | Qt.Edge.TopEdge,
                        9: Qt.Edge.LeftEdge | Qt.Edge.BottomEdge,
                        6: Qt.Edge.RightEdge | Qt.Edge.TopEdge,
                        10: Qt.Edge.RightEdge | Qt.Edge.BottomEdge,
                    }
                    if edges in edge_map:
                        self.windowHandle().startSystemResize(edge_map[edges])
                        return
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event):
            edges = self._edge_at(event.pos())
            cursor_map = {
                1: Qt.CursorShape.SizeHorCursor, 2: Qt.CursorShape.SizeHorCursor,
                4: Qt.CursorShape.SizeVerCursor, 8: Qt.CursorShape.SizeVerCursor,
                5: Qt.CursorShape.SizeFDiagCursor, 10: Qt.CursorShape.SizeFDiagCursor,
                6: Qt.CursorShape.SizeBDiagCursor, 9: Qt.CursorShape.SizeBDiagCursor,
            }
            if edges in cursor_map:
                self.setCursor(cursor_map[edges])
            else:
                self.unsetCursor()
            super().mouseMoveEvent(event)

    # ── Application Setup ──
    app_qt = QApplication(sys.argv)
    app_qt.setApplicationName(APP_NAME)
    app_qt.setQuitOnLastWindowClosed(False)

    icon_path = os.path.join(EXE_DIR, 'icon.ico')
    app_icon = QIcon(icon_path) if os.path.isfile(icon_path) else QIcon()
    app_qt.setWindowIcon(app_icon)

    win = MainWindow()

    # ── System Tray ──
    _really_quit = False

    tray = QSystemTrayIcon(app_icon, app_qt)
    tray.setToolTip('抖音收藏下载器')

    tray_menu = QMenu()
    act_show = QAction('显示主面板', tray_menu)
    act_show.triggered.connect(lambda: (win.show(), win.activateWindow()))
    tray_menu.addAction(act_show)
    tray_menu.addSeparator()
    act_quit = QAction('退出', tray_menu)

    def quit_app():
        nonlocal _really_quit
        _really_quit = True
        tray.hide()
        win.close()
        app_qt.quit()

    act_quit.triggered.connect(quit_app)
    tray_menu.addAction(act_quit)
    tray.setContextMenu(tray_menu)
    tray.activated.connect(lambda reason:
        (win.show(), win.activateWindow())
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
    tray.show()

    def on_close(event):
        # 保存窗口尺寸/位置
        if not win.isMaximized():
            win._settings.setValue('window/x', win.x())
            win._settings.setValue('window/y', win.y())
            win._settings.setValue('window/w', win.width())
            win._settings.setValue('window/h', win.height())
        if _really_quit:
            event.accept()
            return
        event.ignore()
        win.hide()
        tray.showMessage('抖音收藏下载器', '已最小化到系统托盘',
                         QSystemTrayIcon.MessageIcon.Information, 2000)

    win.closeEvent = on_close
    win.show()
    sys.exit(app_qt.exec())


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        err = traceback.format_exc()
        log(f'\n  [致命错误] {e}')
        log(err)
        if IS_WIN:
            ctypes.windll.user32.MessageBoxW(
                0, f'启动失败：\n\n{e}\n\n详细日志：\n{LOG_PATH}',
                '抖音收藏下载器 - 错误', 0x10)
