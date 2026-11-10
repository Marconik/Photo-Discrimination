STYLE = """
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #253249; }
QMainWindow, QWidget#Root { background: #f3f5f9; }
QFrame#TopBar, QFrame#Card { background: #ffffff; border: 1px solid #e1e6ef; border-radius: 12px; }
QFrame#SideBar { background: #162238; border-radius: 12px; }
QLabel#Brand { color: #ffffff; font-size: 17px; font-weight: 700; }
QLabel#SideLabel { color: #a7b6d0; }
QLabel#SideTitle { color: #e3ebf8; font-weight: 600; }
QLabel#SideMetric { color: #ffffff; font-size: 28px; font-weight: 600; }
QLabel#Title { font-size: 23px; font-weight: 700; color: #17263e; }
QLabel#Section { font-size: 15px; font-weight: 600; }
QLabel#Muted { color: #7d899c; }
QLabel#Pill { color: #3864b2; background: #edf3ff; padding: 4px 10px; border-radius: 10px; }
QPushButton { background: #ffffff; border: 1px solid #dce2ec; border-radius: 7px; padding: 8px 13px; font-weight: 500; }
QPushButton:hover { background: #f0f4fb; border-color: #a7badd; }
QPushButton:pressed { background: #e5edf9; }
QPushButton:disabled { color: #a5adba; background: #f3f5f8; border-color: #e8ecf2; }
QPushButton#Primary { background: #426fe9; color: #ffffff; border: 1px solid #426fe9; }
QPushButton#Primary:hover { background: #345fd3; }
QPushButton#Primary:disabled { background: #b7c8f4; border-color: #b7c8f4; }
QPushButton#SideButton { background: #263853; color: #e3ebf8; border: 1px solid #354b6b; }
QPushButton#SideButton:hover { background: #354b6b; }
QLineEdit, QPlainTextEdit { background: #ffffff; border: 1px solid #dce2ec; border-radius: 7px; padding: 9px; selection-background-color: #d7e3ff; }
QLineEdit:focus, QPlainTextEdit:focus { border-color: #6289ef; }
QComboBox { background: #ffffff; border: 1px solid #dce2ec; border-radius: 7px; padding: 9px 12px; min-height: 22px; }
QComboBox:disabled { background: #f3f5f8; color: #a5adba; }
QComboBox QAbstractItemView { background: #ffffff; selection-background-color: #eaf1ff; padding: 6px; }
QTableView { background: #ffffff; alternate-background-color: #fafbfe; border: none; gridline-color: #f1f3f8; selection-background-color: #edf3ff; selection-color: #253249; outline: 0; }
QTableView::item { padding: 9px; border-bottom: 1px solid #edf0f5; }
QTableView::item:selected { background: #eaf1ff; color: #254fa3; }
QHeaderView::section { background: #f7f9fc; color: #78859b; border: none; border-bottom: 1px solid #e8edf5; padding: 10px 9px; font-size: 12px; }
QCheckBox { spacing: 7px; }
QCheckBox::indicator, QTableView::indicator { width: 17px; height: 17px; }
QSplitter::handle { background: transparent; }
QProgressBar { border: none; background: #e7edf8; border-radius: 3px; max-height: 5px; }
QProgressBar::chunk { background: #6289ef; border-radius: 3px; }
QStatusBar { background: #f3f5f9; color: #6e7b90; }
QToolTip { background: #17263e; color: #ffffff; border: none; padding: 5px; }
QScrollBar:vertical { width: 10px; background: #f6f8fc; }
QScrollBar::handle:vertical { background: #cbd5e5; min-height: 30px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""
