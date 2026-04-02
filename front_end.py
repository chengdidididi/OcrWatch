import sys
import sqlite3
import os
import json
import markdown
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QSplitter, QListWidget, 
                               QListWidgetItem, QPlainTextEdit,
                               QSystemTrayIcon, QMenu, QStyle, QLabel, QComboBox,
                               QSizePolicy, QPushButton, QTabWidget,
                               QFileDialog, QLineEdit, QApplication)
from PySide6.QtCore import Qt, QSize, Slot
from PySide6.QtGui import QAction, QIcon, QPixmap, QTextCursor
from PySide6.QtWebEngineWidgets import QWebEngineView

STYLE_SHEET = """
QMainWindow {
    background-color: #f8f9fa;
}
QWidget {
    font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 10pt;
    color: #202124;
}
/* 定义全局标签的标准色与字重 */
QLabel {
    color: #5f6368;
    font-weight: bold;
}
/* 选项卡基础样式与交互反馈 */
QTabWidget::pane {
    border: 1px solid #dadce0;
    /* 取消左上角圆角以实现与活动标签页下边缘的视觉融合 */
    border-top-left-radius: 0px;
    border-top-right-radius: 8px;
    border-bottom-left-radius: 8px;
    border-bottom-right-radius: 8px;
    background-color: #ffffff;
    top: -1px;
}
QTabBar::tab {
    background-color: #f1f3f4;
    border: 1px solid #dadce0;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 8px 16px;
    margin-right: 2px;
    color: #5f6368;
}
QTabBar::tab:selected {
    background-color: #ffffff;
    color: #1a73e8;
    font-weight: bold;
    border-bottom: 1px solid #ffffff;
}
QTabBar::tab:hover:!selected {
    background-color: #e8eaed;
}
QComboBox {
    background-color: #ffffff;
    border: 1px solid #dadce0;
    border-radius: 6px;
    padding: 4px 12px;
    color: #5f6368;
    min-width: 120px;
}
QComboBox:hover {
    border-color: #bdc1c6;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QPushButton {
    background-color: #ffffff;
    border: 1px solid #dadce0;
    border-radius: 6px;
    padding: 6px 16px;
    color: #5f6368;
}
QPushButton:hover {
    background-color: #f1f3f4;
}
QPushButton#LatexBtn {
    background-color: #f1f3f4;
    color: #5f6368;
    border: 1px solid #dadce0;
    font-weight: bold;
}
QPushButton#LatexBtn:checked {
    background-color: #e8f0fe;
    color: #1a73e8;
    border-color: #1a73e8;
}
QSplitter::handle {
    background-color: transparent;
    width: 6px;
}
QSplitter::handle:hover {
    background-color: #dadce0;
}
QListWidget {
    background-color: #ffffff;
    border: none;
    outline: none;
}
QListWidget::item {
    padding: 8px;
    border-bottom: 1px solid #f1f3f4;
}
QListWidget::item:selected {
    background-color: #e8f0fe;
    color: #1a73e8;
    border-left: 4px solid #1a73e8;
}
QListWidget::item:hover:!selected {
    background-color: #f8f9fa;
}
QPlainTextEdit {
    background-color: #ffffff;
    border: 1px solid #dadce0;
    border-radius: 8px;
    padding: 10px;
    selection-background-color: #c6dafc;
}
QWebEngineView {
    background-color: #ffffff;
    border: 1px solid #dadce0;
    border-radius: 8px;
}
QScrollBar:vertical {
    border: none;
    background: transparent;
    width: 6px;
    margin: 0px;
}
QScrollBar::handle:vertical {
    background: #bdc1c6;
    border-radius: 3px;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover {
    background: #9aa0a6;
}
/* 限定处理进度页面的外层容器为圆角矩形 */
QWidget#ProgressContainer {
    background-color: #ffffff;
    border: 1px solid #dadce0;
    border-radius: 12px;
}
QProgressBar {
    border: 1px solid #dadce0;
    border-radius: 6px;
    text-align: center;
    background-color: #f8f9fa;
    color: #5f6368;
    font-weight: bold;
}
QProgressBar::chunk {
    background-color: #1a73e8;
    border-radius: 5px;
}
"""


class ImagePreviewLabel(QLabel):
    # 承载原始图像并执行自适应比例缩放渲染
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._raw_pixmap = None
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background-color: #f1f3f4; border-radius: 8px; color: #9aa0a6;")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(1, 1)
        
    def setPixmap(self, pixmap):
        self._raw_pixmap = pixmap
        self._scale_and_render()

    def resizeEvent(self, event):
        if self._raw_pixmap:
            self._scale_and_render()
        super().resizeEvent(event)

    def _scale_and_render(self):
        if not self._raw_pixmap or self._raw_pixmap.isNull():
            return
        scaled_pixmap = self._raw_pixmap.scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        super().setPixmap(scaled_pixmap)

class OCRMainWindow(QMainWindow):
    def __init__(self, config_file="config.json"):
        super().__init__()
        self.setWindowTitle("OCR 结果管理器")
        self.resize(1000, 700)
        self.setStyleSheet(STYLE_SHEET)
        self.setWindowIcon(QIcon("frog.ico"))
        
        # 挂载本地配置文件路径并执行初始化读取
        self.config_file = config_file
        self.config = self._load_config()
        self.db_path = self.config.get("db_path", "")
        self.image_dir = self.config.get("watch_dir", "")
        self.api_token = self.config.get("api_token", "")

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(16, 16, 16, 16)

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        self._init_ocr_tab()
        self._init_progress_tab()
        self._init_settings_tab()

        self.tabs.addTab(self.tab_ocr, "OCR 结果")
        self.tabs.addTab(self.tab_progress, "处理进度与日志")
        self.tabs.addTab(self.tab_settings, "设置")

        self._setup_system_tray()
        
        self.thumbnail_list.itemClicked.connect(self._on_item_clicked)
        self.thumbnail_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        
        self.load_data_from_db()

    def _load_config(self):
        # 探测并反序列化同目录下的JSON文件以恢复上次设定的状态
        default_config = {"db_path": "", "watch_dir": "", "api_token": ""}
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    default_config.update(json.load(f))
            except Exception as e:
                print(f"读取配置文件失败: {e}")
        return default_config

    def _save_config(self):
        # 提取当前三个输入框的内容并覆盖写入磁盘配置文件
        self.db_path = self.le_db_path.text()
        self.image_dir = self.le_watch_dir.text()
        self.api_token = self.le_api_token.text()
        
        config = {
            "db_path": self.db_path,
            "watch_dir": self.image_dir,
            "api_token": self.api_token
        }
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"保存配置文件失败: {e}")

    @Slot(str)
    def append_log(self, text):
        # 驱动进度界面的光标移动并插入增量文本
        cursor = self.log_output.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self.log_output.setTextCursor(cursor)
        self.log_output.ensureCursorVisible()

    def _init_ocr_tab(self):
        self.tab_ocr = QWidget()
        layout = QVBoxLayout(self.tab_ocr)
        layout.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(8)
        layout.addWidget(splitter)

        self.thumbnail_list = QListWidget()
        self.thumbnail_list.setMinimumWidth(220)
        self.thumbnail_list.setIconSize(QSize(180, 100))
        splitter.addWidget(self.thumbnail_list)

        middle_container = QWidget()
        middle_layout = QVBoxLayout(middle_container)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        middle_layout.setSpacing(10)
        
        self.image_preview = ImagePreviewLabel("请在左侧选择项目以预览图像")
        self.raw_text_edit = QPlainTextEdit()
        self.raw_text_edit.setPlaceholderText("这里将显示识别出的纯文本...")
        
        middle_layout.addWidget(self.image_preview, 1)
        middle_layout.addWidget(self.raw_text_edit, 1)
        splitter.addWidget(middle_container)

        self.html_view = QWebEngineView()
        self.html_view.setHtml("<body style='font-family: sans-serif; color: #a0a0a0; padding: 20px;'>这里将渲染富文本排版...</body>")
        splitter.addWidget(self.html_view)

        splitter.setSizes([220, 400, 400])

    def _init_progress_tab(self):
        self.tab_progress = QWidget()
        layout = QVBoxLayout(self.tab_progress)
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText("系统日志和识别进度将显示在这里...")
        layout.addWidget(self.log_output)

    def _init_settings_tab(self):
        # 构筑表单布局并将内容变更信号直接绑定到配置文件持久化方法上
        self.tab_settings = QWidget()
        layout = QVBoxLayout(self.tab_settings)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(25)
        layout.setAlignment(Qt.AlignTop)

        # 数据库设置
        db_layout = QHBoxLayout()
        lbl_db = QLabel("数据库文件:")
        lbl_db.setFixedWidth(120)
        self.le_db_path = QLineEdit(self.db_path)
        self.le_db_path.setReadOnly(True)
        self.btn_browse_db = QPushButton("浏览...")
        self.btn_browse_db.clicked.connect(self._browse_db)
        db_layout.addWidget(lbl_db)
        db_layout.addWidget(self.le_db_path)
        db_layout.addWidget(self.btn_browse_db)

        # 监控目录设置
        watch_layout = QHBoxLayout()
        lbl_watch = QLabel("监控目录:")
        lbl_watch.setFixedWidth(120)
        self.le_watch_dir = QLineEdit(self.image_dir)
        self.le_watch_dir.setReadOnly(True)
        self.btn_browse_watch = QPushButton("浏览...")
        self.btn_browse_watch.clicked.connect(self._browse_watch)
        watch_layout.addWidget(lbl_watch)
        watch_layout.addWidget(self.le_watch_dir)
        watch_layout.addWidget(self.btn_browse_watch)

        # API Token 设置
        api_layout = QHBoxLayout()
        lbl_api = QLabel("API Token:")
        lbl_api.setFixedWidth(120)
        self.le_api_token = QLineEdit(self.api_token)
        self.le_api_token.setEchoMode(QLineEdit.Password)  # 隐藏输入内容
        self.le_api_token.editingFinished.connect(self._save_config) # 失去焦点或按回车时保存
        api_layout.addWidget(lbl_api)
        api_layout.addWidget(self.le_api_token)

        # 其他设置保留
        sort_layout = QHBoxLayout()
        lbl_sort = QLabel("列表排序依据:")
        lbl_sort.setFixedWidth(120)
        self.combo_sort = QComboBox()
        self.combo_sort.addItems(["按时间排序", "按名称排序"])
        self.combo_sort.currentIndexChanged.connect(self.load_data_from_db)
        sort_layout.addWidget(lbl_sort)
        sort_layout.addWidget(self.combo_sort)
        sort_layout.addStretch()

        latex_layout = QHBoxLayout()
        lbl_latex = QLabel("富文本渲染引擎:")
        lbl_latex.setFixedWidth(120)
        self.btn_toggle_latex = QPushButton("LaTeX 解析: 已开启")
        self.btn_toggle_latex.setObjectName("LatexBtn")
        self.btn_toggle_latex.setCheckable(True)
        self.btn_toggle_latex.setChecked(True)
        # 拦截状态翻转事件并转交专属处理方法以同步更新UI文本
        self.btn_toggle_latex.toggled.connect(self._on_latex_toggled)
        latex_layout.addWidget(lbl_latex)
        latex_layout.addWidget(self.btn_toggle_latex)
        latex_layout.addStretch()
        
        # 底部提示语
        lbl_tip = QLabel("注意：修改 数据库、监控目录 或 API Token 后，需重启本应用以使后台进程生效。")
        lbl_tip.setStyleSheet("color: #d93025; font-size: 9pt;")

        layout.addLayout(db_layout)
        layout.addLayout(watch_layout)
        layout.addLayout(api_layout)
        layout.addLayout(sort_layout)
        layout.addLayout(latex_layout)
        layout.addStretch()
        layout.addWidget(lbl_tip)

    def _browse_db(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择数据库文件", "", "SQLite 数据库 (*.db *.sqlite);;所有文件 (*.*)"
        )
        if file_path:
            self.le_db_path.setText(file_path)
            self._save_config()
            self.load_data_from_db()

    def _browse_watch(self):
        # 唤起目录选择器并更新内部状态以及配置文件
        dir_path = QFileDialog.getExistingDirectory(self, "选择 ShareX 截图目录", self.le_watch_dir.text())
        if dir_path:
            self.le_watch_dir.setText(dir_path)
            self._save_config()

    def _setup_system_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        icon = QIcon("frog.ico")
        self.tray_icon.setIcon(icon)
        
        tray_menu = QMenu()
        show_action = QAction("显示主窗口", self)
        show_action.triggered.connect(self.showNormal)
        tray_menu.addAction(show_action)
        quit_action = QAction("完全退出", self)
        quit_action.triggered.connect(QApplication.instance().quit)
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_icon_activated)
        self.tray_icon.show()

    def _on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self.showNormal()
                self.activateWindow()

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    @Slot()
    def load_data_from_db(self):
        # 读取数据库游标数据并装载至侧边栏列表控件
        self.thumbnail_list.clear()
        if not self.db_path or not os.path.exists(self.db_path):
            return

        sort_mode = self.combo_sort.currentText()
        order_clause = "ORDER BY file_name ASC" if sort_mode == "按名称排序" else "ORDER BY id DESC"

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(f"SELECT id, file_name, extracted_text FROM ocr_records {order_clause}")
            
            for row in cursor.fetchall():
                record_id,file_name, extracted_text = row
                # 直接将数据库读取的完整路径指派为图像加载路径
                image_path = file_name 
                # 强制通过反斜杠切割字符串，取最后一段作为列表显示名称
                display_name = file_name.split('\\')[-1] if '\\' in file_name else file_name.split('/')[-1]

                item = QListWidgetItem(display_name)
                if os.path.exists(image_path):
                    item.setIcon(QIcon(image_path))
                    
                item.setSizeHint(QSize(200, 140))
                item.setData(Qt.ItemDataRole.UserRole, {
                    "raw_text": extracted_text,
                    "image_path": image_path
                })
                self.thumbnail_list.addItem(item)
        except Exception as e:
            print(f"读取数据库失败: {e}")
        finally:
            if 'conn' in locals():
                conn.close()

    def _on_latex_toggled(self, checked):
        # 依据传入的布尔值刷新按钮文本，并触发富文本区域的重新渲染
        if checked:
            self.btn_toggle_latex.setText("LaTeX 解析: 已开启")
        else:
            self.btn_toggle_latex.setText("LaTeX 解析: 已关闭")
        self._refresh_current_view()
        
    def _refresh_current_view(self):
        current_item = self.thumbnail_list.currentItem()
        if current_item:
            self._on_item_clicked(current_item)

    def _on_item_clicked(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if data:
            raw_text = data.get("raw_text", "")
            self.raw_text_edit.setPlainText(raw_text)
            
            html_content = markdown.markdown(raw_text, extensions=['tables', 'fenced_code'])
            
            math_config = "inlineMath: [['$', '$'], ['\\\\(', '\\\\)']]" if self.btn_toggle_latex.isChecked() else "inlineMath: [['\\\\(', '\\\\)']]"
            
            mathjax_template = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <script>
                    window.MathJax = {{
                        tex: {{ {math_config}, displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']] }},
                        svg: {{ fontCache: 'global' }}
                    }};
                </script>
                <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
                <style>
                    body {{ font-family: "Microsoft YaHei", sans-serif; padding: 10px; color: #202124; line-height: 1.6; }}
                    table {{ border-collapse: collapse; width: 100%; margin-top: 10px; }}
                    table, th, td {{ border: 1px solid #dadce0; padding: 8px; }}
                </style>
            </head>
            <body>{html_content}</body>
            </html>
            """
            self.html_view.setHtml(mathjax_template)
            
            img_path = data.get("image_path")
            if img_path and os.path.exists(img_path):
                self.image_preview.setPixmap(QPixmap(img_path))
            else:
                self.image_preview.clear()
                self.image_preview.setText("源文件已丢失")

    def _on_item_double_clicked(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if data and "image_path" in data and os.path.exists(data["image_path"]):
            os.startfile(data["image_path"])