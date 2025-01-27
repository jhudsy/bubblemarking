# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'gui.ui'
##
## Created by: Qt User Interface Compiler version 6.7.3
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QApplication, QCheckBox, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMenuBar, QPushButton,
    QSizePolicy, QStatusBar, QTextBrowser, QVBoxLayout,
    QWidget)

class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        if not MainWindow.objectName():
            MainWindow.setObjectName(u"MainWindow")
        MainWindow.resize(537, 569)
        self.centralwidget = QWidget(MainWindow)
        self.centralwidget.setObjectName(u"centralwidget")
        self.layoutWidget = QWidget(self.centralwidget)
        self.layoutWidget.setObjectName(u"layoutWidget")
        self.layoutWidget.setGeometry(QRect(10, 10, 371, 251))
        self.verticalLayout = QVBoxLayout(self.layoutWidget)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.verticalLayout.setContentsMargins(0, 0, 0, 0)
        self.horizontalLayout = QHBoxLayout()
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.ScanFileLabel = QLabel(self.layoutWidget)
        self.ScanFileLabel.setObjectName(u"ScanFileLabel")

        self.horizontalLayout.addWidget(self.ScanFileLabel)

        self.ScanFileName = QLineEdit(self.layoutWidget)
        self.ScanFileName.setObjectName(u"ScanFileName")

        self.horizontalLayout.addWidget(self.ScanFileName)

        self.ScanFileSelectButton = QPushButton(self.layoutWidget)
        self.ScanFileSelectButton.setObjectName(u"ScanFileSelectButton")

        self.horizontalLayout.addWidget(self.ScanFileSelectButton)


        self.verticalLayout.addLayout(self.horizontalLayout)

        self.OneAnswerCheckbox = QCheckBox(self.layoutWidget)
        self.OneAnswerCheckbox.setObjectName(u"OneAnswerCheckbox")
        self.OneAnswerCheckbox.setChecked(True)

        self.verticalLayout.addWidget(self.OneAnswerCheckbox)

        self.AnswerInFileCheckbox = QCheckBox(self.layoutWidget)
        self.AnswerInFileCheckbox.setObjectName(u"AnswerInFileCheckbox")
        self.AnswerInFileCheckbox.setChecked(True)

        self.verticalLayout.addWidget(self.AnswerInFileCheckbox)

        self.horizontalLayout_2 = QHBoxLayout()
        self.horizontalLayout_2.setObjectName(u"horizontalLayout_2")
        self.AnswerFileLabel = QLabel(self.layoutWidget)
        self.AnswerFileLabel.setObjectName(u"AnswerFileLabel")
        self.AnswerFileLabel.setEnabled(False)

        self.horizontalLayout_2.addWidget(self.AnswerFileLabel)

        self.AnswerFileName = QLineEdit(self.layoutWidget)
        self.AnswerFileName.setObjectName(u"AnswerFileName")
        self.AnswerFileName.setEnabled(False)

        self.horizontalLayout_2.addWidget(self.AnswerFileName)

        self.AnswerFileSelectButton = QPushButton(self.layoutWidget)
        self.AnswerFileSelectButton.setObjectName(u"AnswerFileSelectButton")
        self.AnswerFileSelectButton.setEnabled(False)

        self.horizontalLayout_2.addWidget(self.AnswerFileSelectButton)


        self.verticalLayout.addLayout(self.horizontalLayout_2)

        self.horizontalLayout_3 = QHBoxLayout()
        self.horizontalLayout_3.setObjectName(u"horizontalLayout_3")
        self.OutputFileLabel = QLabel(self.layoutWidget)
        self.OutputFileLabel.setObjectName(u"OutputFileLabel")

        self.horizontalLayout_3.addWidget(self.OutputFileLabel)

        self.OutputFileName = QLineEdit(self.layoutWidget)
        self.OutputFileName.setObjectName(u"OutputFileName")

        self.horizontalLayout_3.addWidget(self.OutputFileName)

        self.OutputFileSelectButton = QPushButton(self.layoutWidget)
        self.OutputFileSelectButton.setObjectName(u"OutputFileSelectButton")

        self.horizontalLayout_3.addWidget(self.OutputFileSelectButton)


        self.verticalLayout.addLayout(self.horizontalLayout_3)

        self.ScanButton = QPushButton(self.layoutWidget)
        self.ScanButton.setObjectName(u"ScanButton")

        self.verticalLayout.addWidget(self.ScanButton)

        self.OutputTextArea = QTextBrowser(self.centralwidget)
        self.OutputTextArea.setObjectName(u"OutputTextArea")
        self.OutputTextArea.setGeometry(QRect(10, 270, 521, 221))
        self.ClearOutputButton = QPushButton(self.centralwidget)
        self.ClearOutputButton.setObjectName(u"ClearOutputButton")
        self.ClearOutputButton.setGeometry(QRect(10, 500, 111, 26))
        MainWindow.setCentralWidget(self.centralwidget)
        self.menubar = QMenuBar(MainWindow)
        self.menubar.setObjectName(u"menubar")
        self.menubar.setGeometry(QRect(0, 0, 537, 21))
        MainWindow.setMenuBar(self.menubar)
        self.statusbar = QStatusBar(MainWindow)
        self.statusbar.setObjectName(u"statusbar")
        MainWindow.setStatusBar(self.statusbar)

        self.retranslateUi(MainWindow)
        self.AnswerInFileCheckbox.clicked["bool"].connect(self.AnswerFileName.setDisabled)
        self.AnswerInFileCheckbox.clicked["bool"].connect(self.AnswerFileSelectButton.setDisabled)
        self.ClearOutputButton.clicked.connect(self.OutputTextArea.clear)
        self.AnswerInFileCheckbox.clicked["bool"].connect(self.AnswerFileLabel.setDisabled)

        QMetaObject.connectSlotsByName(MainWindow)
    # setupUi

    def retranslateUi(self, MainWindow):
        MainWindow.setWindowTitle(QCoreApplication.translate("MainWindow", u"MCQ Scanning", None))
        self.ScanFileLabel.setText(QCoreApplication.translate("MainWindow", u"Scan file", None))
        self.ScanFileSelectButton.setText(QCoreApplication.translate("MainWindow", u"Select", None))
        self.OneAnswerCheckbox.setText(QCoreApplication.translate("MainWindow", u"Warn if there is more than one answer per question", None))
        self.AnswerInFileCheckbox.setText(QCoreApplication.translate("MainWindow", u"Answers in scan file (matriculation number 00000000)", None))
        self.AnswerFileLabel.setText(QCoreApplication.translate("MainWindow", u"Answer file", None))
        self.AnswerFileSelectButton.setText(QCoreApplication.translate("MainWindow", u"Select", None))
        self.OutputFileLabel.setText(QCoreApplication.translate("MainWindow", u"Output file", None))
        self.OutputFileSelectButton.setText(QCoreApplication.translate("MainWindow", u"Select", None))
        self.ScanButton.setText(QCoreApplication.translate("MainWindow", u"Scan", None))
        self.ClearOutputButton.setText(QCoreApplication.translate("MainWindow", u"Clear output box", None))
    # retranslateUi

