from gui import Ui_MainWindow
from PyQt6 import QtCore, QtGui, QtWidgets
import pandas as pd

import scan
import logging

class WriteLogToWidgetHandler(logging.Handler):
    def __init__(self, widget):
        super().__init__()
        self.widget = widget

    def emit(self, record):
        msg = self.format(record)
        self.widget.append(msg)

#add the custom handler to the root logger

class AppMainWindow(Ui_MainWindow):
    def __init__(self, window):
        self.setupUi(window)
        self.ScanFileSelectButton.clicked.connect(self.select_scan_file)
        self.AnswerFileSelectButton.clicked.connect(self.select_answer_file)
        self.OutputFileSelectButton.clicked.connect(self.select_output_file)

        self.OutputFileName.setText("output.csv")

        self.ScanButton.clicked.connect(self.run_scan)

        self.menubar.setNativeMenuBar(True)

        logger = logging.getLogger()
        handler = WriteLogToWidgetHandler(widget=self.OutputTextArea)
        handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
        logger.addHandler(handler)

    def actionExit(self):
        sys.exit()

    def select_scan_file(self):
        file = QtWidgets.QFileDialog.getOpenFileName(filter="PDF files (*.pdf)")[0]
        self.ScanFileName.setText(file)

    def select_answer_file(self):
        file = QtWidgets.QFileDialog.getOpenFileName(filter="CSV files (*.csv);; XLSX files (*.xlsx)")[0]
        self.AnswerFileName.setText(file)

    def select_output_file(self):
        file = QtWidgets.QFileDialog.getSaveFileName()[0]
        self.OutputFileName.setText(file)

    def run_scan(self):
        scan_file = self.ScanFileName.text()
        answer_file = self.AnswerFileName.text()
        output_file = self.OutputFileName.text()
        one_answer_only = self.OneAnswerCheckbox.isChecked()
        answer_in_file = self.AnswerInFileCheckbox.isChecked()

        doc = None
        try:
            doc = scan.get_file(scan_file)
        except FileNotFoundError:
            QtWidgets.QMessageBox.warning(self.ScanButton, "Error", "Unable to open file")
            return
        
        num_pages = scan.get_number_of_pages(doc)
        logging.info(f"Number of pages in document: {num_pages}")

        self.OutputTextArea.append("Scan complete")
        student_answer_df = pd.DataFrame(columns=["Matriculation number","Question","Answer"]) #stores student answers

        unknown_matriculation_number = 99999999 #unknown matriculation number counter

        #read the answers from the scanned image noting any issues.    
        for i in range(num_pages):
            df = scan.read_image_answers(scan.get_image_from_file(doc,i),ONE_ANSWER_ONLY=one_answer_only)
            if df["Matriculation number"].values[0] == "99999999":
                logging.warning("Unable to read matriculation number on page "+str(i)+". Assigning matriculation number "+str(unknown_matriculation_number))
                df["Matriculation number"] = unknown_matriculation_number
                unknown_matriculation_number -= 1
            else:
                logging.info("Read matriculation number "+str(df["Matriculation number"].values[0])+" on page "+str(i))
        
        if df["Matriculation number"].values[0] in student_answer_df["Matriculation number"].values:
            logging.warning("Duplicate matriculation number "+str(df["Matriculation number"].values[0])+" on page "+str(i)+"setting to"+str(unknown_matriculation_number))
            df["Matriculation number"] = unknown_matriculation_number
            unknown_matriculation_number -= 1 
            
        pd.concat([student_answer_df,df],ignore_index=True)

        answers_df = None
        if not answer_in_file:
            try:
                answers_df = scan.read_answers_from_file(answer_file)
            except FileNotFoundError:
                QtWidgets.QMessageBox.warning(self.ScanButton, "Error", "Unable to open answer file")
                return
        else:
            answers_df = scan.read_answers_from_df(student_answer_df)
        
        student_answer_df = scan.compute_marks(student_answer_df,answers_df)
        output_df = scan.make_output_df(student_answer_df,answers_df)

        output_df.to_csv(output_file,index=False)
        logging.info("Output file written to ",output_file)

if __name__ == "__main__":
    import sys
    app = QtWidgets.QApplication(sys.argv)
    MainWindow = QtWidgets.QMainWindow()
    ui = AppMainWindow(MainWindow)
    MainWindow.show()
    sys.exit(app.exec())
    