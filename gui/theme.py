PRIMARY = "#2563EB"
DANGER = "#EF4444"
SUCCESS = "#16A34A"
BACKGROUND = "#F5F7FB"
CARD = "#FFFFFF"
BORDER = "#E5E7EB"
TEXT = "#111827"
MUTED = "#6B7280"
INPUT_BORDER = "#CBD5E1"
NAV_SELECTED = "#EAF2FF"
TABLE_HEAD = "#F8FAFC"
TABLE_ALT = "#FAFAFA"


APP_QSS = f"""
* {{
    font-family: "Microsoft YaHei UI", "Segoe UI", Arial, sans-serif;
    font-size: 13px;
    color: {TEXT};
}}

QMainWindow {{
    background: {BACKGROUND};
}}

QWidget#Sidebar {{
    background: {CARD};
    border-right: 1px solid {BORDER};
}}

QLabel#Brand {{
    background: {CARD};
    font-size: 14px;
    font-weight: 700;
    padding-left: 16px;
    border-bottom: 1px solid {BORDER};
}}

QListWidget#Navigation {{
    background: {CARD};
    border: none;
    padding: 12px 8px;
    outline: 0;
}}

QListWidget#Navigation::item {{
    min-height: 40px;
    padding-left: 14px;
    border-radius: 8px;
}}

QListWidget#Navigation::item:hover {{
    background: #F3F4F6;
}}

QListWidget#Navigation::item:selected {{
    background: {NAV_SELECTED};
    color: {PRIMARY};
    font-weight: 700;
    border-left: 3px solid {PRIMARY};
}}

QListWidget#GroupList {{
    background: {CARD};
    border: none;
    outline: 0;
}}

QListWidget#GroupList::item {{
    min-height: 36px;
    padding-left: 12px;
    border-radius: 8px;
    font-weight: 600;
}}

QListWidget#GroupList::item:hover {{
    background: #F3F4F6;
}}

QListWidget#GroupList::item:selected {{
    background: {NAV_SELECTED};
    color: {PRIMARY};
}}

QLabel#PageTitle {{
    font-size: 22px;
    font-weight: 800;
}}

QLabel#PageHint {{
    color: {MUTED};
}}

QLabel#SectionTitle {{
    font-size: 15px;
    font-weight: 800;
}}

QLabel#Muted {{
    color: {MUTED};
}}

QFrame#Card {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}

QPushButton {{
    min-height: 34px;
    padding: 0 16px;
    border-radius: 8px;
    border: 1px solid {INPUT_BORDER};
    background: {CARD};
    font-weight: 600;
}}

QPushButton:hover {{
    background: #F8FAFC;
    border-color: #94A3B8;
}}

QPushButton#PrimaryButton {{
    background: {PRIMARY};
    color: #FFFFFF;
    border-color: {PRIMARY};
}}

QPushButton#PrimaryButton:hover {{
    background: #1D4ED8;
    border-color: #1D4ED8;
}}

QPushButton#DangerButton {{
    background: #FEF2F2;
    color: {DANGER};
    border-color: #FCA5A5;
}}

QPushButton#DangerButton:hover {{
    background: #FEE2E2;
    border-color: {DANGER};
}}

QLineEdit, QComboBox {{
    min-height: 34px;
    border: 1px solid {INPUT_BORDER};
    border-radius: 6px;
    padding: 0 10px;
    background: {CARD};
}}

QLineEdit:focus, QComboBox:focus {{
    border-color: {PRIMARY};
}}

QTableWidget {{
    background: {CARD};
    alternate-background-color: {TABLE_ALT};
    border: 1px solid {BORDER};
    border-radius: 10px;
    gridline-color: {BORDER};
    selection-background-color: #DBEAFE;
    selection-color: {TEXT};
}}

QTableWidget::item {{
    padding: 6px 8px;
    border-color: {BORDER};
}}

QHeaderView::section {{
    background: {TABLE_HEAD};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 8px;
    font-weight: 800;
}}

QTextEdit {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 10px;
    font-family: Consolas, "Microsoft YaHei UI", monospace;
}}

QTabBar::tab {{
    min-height: 34px;
    min-width: 92px;
    padding: 0 14px;
    color: {MUTED};
}}

QTabBar::tab:hover {{
    color: {PRIMARY};
}}

QTabBar::tab:selected {{
    color: {PRIMARY};
    font-weight: 800;
    border-bottom: 2px solid {PRIMARY};
}}

QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 10px;
    background: {CARD};
}}

QCheckBox {{
    spacing: 8px;
}}
"""
