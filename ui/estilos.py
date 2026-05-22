"""Folhas de estilo (QSS) compartilhadas pelos widgets do plugin."""


HEADER_STYLE = """
    QFrame {
        background-color: #1a5276;
    }
"""

FOOTER_STYLE = """
    QFrame {
        background-color: #1e2a33;
    }
"""


def tab_style() -> str:
    return """
        QTabWidget::pane {
            border: none;
            background-color: #ffffff;
        }
        QTabBar::tab {
            background-color: #e8edf1;
            color: #34495e;
            padding: 10px 22px;
            margin-right: 2px;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
            font-weight: bold;
            min-width: 180px;
        }
        QTabBar::tab:selected {
            background-color: #27ae60;
            color: white;
        }
        QTabBar::tab:hover:!selected {
            background-color: #d5dbdb;
        }
    """


def groupbox_style() -> str:
    return """
        QGroupBox {
            font-weight: bold;
            font-size: 12px;
            color: #2c3e50;
            border: 1px solid #d5d8dc;
            border-radius: 6px;
            margin-top: 12px;
            padding-top: 12px;
            background-color: white;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 6px;
            color: #1a5276;
        }
    """


def botao_style(tipo: str = "primary") -> str:
    cores = {
        "primary":   ("#1a5276", "#154360", "white"),
        "secondary": ("#7f8c8d", "#626f70", "white"),
        "success":   ("#27ae60", "#1e8449", "white"),
        "danger":    ("#c0392b", "#922b21", "white"),
        "warning":   ("#f39c12", "#d68910", "white"),
        "info":      ("#2980b9", "#1f618d", "white"),
    }
    bg, hover, text = cores.get(tipo, cores["primary"])
    return f"""
        QPushButton {{
            background-color: {bg};
            color: {text};
            border: none;
            padding: 8px 16px;
            border-radius: 4px;
            font-weight: bold;
        }}
        QPushButton:hover {{
            background-color: {hover};
        }}
        QPushButton:disabled {{
            background-color: #bdc3c7;
            color: #7f8c8d;
        }}
    """


def parecer_style(aprovado: bool) -> str:
    cor_bg = "#d5f5e3" if aprovado else "#fadbd8"
    cor_borda = "#27ae60" if aprovado else "#c0392b"
    cor_texto = "#1e8449" if aprovado else "#922b21"
    return f"""
        QLabel {{
            background-color: {cor_bg};
            border: 2px solid {cor_borda};
            color: {cor_texto};
            padding: 12px;
            border-radius: 6px;
            font-size: 14px;
            font-weight: bold;
        }}
    """
