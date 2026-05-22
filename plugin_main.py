"""Classe principal do plugin - registra ações no QGIS."""

import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox


class ValidadorBasesReferenciaPlugin:
    """Plugin de validação e adequação de bases geográficas de referência."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.actions = []
        self.menu = "&Validador de Bases de Referência"
        self.toolbar = None
        self.main_window = None
        self.action_main = None
        self.action_about = None

    def _icon_path(self):
        path_svg = os.path.join(self.plugin_dir, "icones", "logo.svg")
        path_png = os.path.join(self.plugin_dir, "icones", "logo.png")
        if os.path.exists(path_svg):
            return path_svg
        if os.path.exists(path_png):
            return path_png
        return ""

    def initGui(self):
        """Cria toolbar dedicada e menu do plugin."""
        self.toolbar = self.iface.addToolBar("Validador de Bases de Referência")
        self.toolbar.setObjectName("ValidadorBasesReferenciaToolbar")

        icon = QIcon(self._icon_path())

        self.action_main = QAction(
            icon,
            "Validador de Bases de Referência",
            self.iface.mainWindow(),
        )
        self.action_main.setStatusTip(
            "Abre a janela do Validador de Bases de Referência"
        )
        self.action_main.setWhatsThis(
            "Validação de qualidade e adequação de bases geográficas de referência."
        )
        self.action_main.triggered.connect(self.run)
        self.toolbar.addAction(self.action_main)
        self.iface.addPluginToMenu(self.menu, self.action_main)
        self.actions.append(self.action_main)

        self.action_about = QAction(
            QIcon.fromTheme("help-about"),
            "Sobre",
            self.iface.mainWindow(),
        )
        self.action_about.triggered.connect(self.show_about)
        self.iface.addPluginToMenu(self.menu, self.action_about)
        self.actions.append(self.action_about)

    def unload(self):
        """Remove ações do menu e toolbar ao desativar o plugin."""
        for action in self.actions:
            self.iface.removePluginMenu(self.menu, action)

        if self.toolbar:
            del self.toolbar
            self.toolbar = None

        if self.main_window is not None:
            try:
                self.main_window.close()
            except Exception:
                pass
            self.main_window = None

    def run(self):
        """Abre (ou traz para frente) a janela principal do plugin."""
        if self.main_window is None:
            from .ui.main_window import MainWindow
            self.main_window = MainWindow(self.iface)

        self.main_window.show()
        self.main_window.raise_()
        self.main_window.activateWindow()

    def show_about(self):
        """Exibe o diálogo Sobre."""
        QMessageBox.about(
            self.iface.mainWindow(),
            "Sobre - Validador de Bases de Referência",
            (
                "<h2>Validador de Bases de Referência</h2>"
                "<p><b>Versão:</b> 1.0.0</p>"
                "<p>Plugin para validação de qualidade e adequação de bases"
                " geográficas de referência.</p>"
                "<p><b>Funcionalidades</b></p>"
                "<ul>"
                "<li>Validação de qualidade (amostragem, rotulagem, matriz de"
                " confusão, Kappa, PEC).</li>"
                "<li>Adequação de bases conforme padrão escolhido.</li>"
                "<li>Geração de APP hídrica a partir da hidrografia.</li>"
                "</ul>"
            ),
        )
