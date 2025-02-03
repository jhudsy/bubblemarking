from bubblemarking.gui.gui import Ui_MainWindow
from PySide6 import QtWidgets
import pandas as pd
import sys

import bubblemarking.scanning as scanning
import bubblemarking.dataframes as dataframes
import logging



class WriteLogToWidgetHandler(logging.Handler):
    def __init__(self, widget):
        super().__init__()
        self.widget = widget

    def emit(self, record):
        msg = self.format(record)
        self.widget.append(msg)

class AppMainWindow(Ui_MainWindow):
    def __init__(self, window):
        self.setupUi(window)
        self.ScanFileSelectButton.clicked.connect(self.select_scan_file)
        self.AnswerFileSelectButton.clicked.connect(self.select_answer_file)
        self.OutputFileSelectButton.clicked.connect(self.select_output_file)
        self.ImageFileSelectButton.clicked.connect(self.select_image_file)

        self.OutputFileName.setText("output.csv")
        self.ImageFileName.setText("output.pdf")

        self.ScanButton.clicked.connect(self.run_scan)

        self.menubar.setNativeMenuBar(True)

        logger = logging.getLogger()
        logger.setLevel(logging.DEBUG)
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
    
    def select_image_file(self):
        file = QtWidgets.QFileDialog.getSaveFileName(filter="PDF files (*.pdf)")[0]
        self.ImageFileName.setText(file)

    def run_scan(self):
        scan_file = self.ScanFileName.text()
        answer_file = self.AnswerFileName.text()
        output_file = self.OutputFileName.text()
        one_answer_only = self.OneAnswerCheckbox.isChecked()
        answer_in_file = self.AnswerInFileCheckbox.isChecked()
        write_image_file = self.SaveImageFileCheckbox.isChecked()
        pdf = None

        if write_image_file:
            #create a pdf file to store the images
            pdf = scanning.create_pdf()
        
        num_questions = None

        answers_df = pd.DataFrame()
        if not answer_in_file: #if answers ARE in a separate file then we read it now to get the number of questions
            try:
                answers_df = dataframes.read_answers_from_file(answer_file)
                num_questions = len(answers_df)
            except FileNotFoundError:
                QtWidgets.QMessageBox.warning(self.ScanButton, "Error", "Unable to open answer file")
                return

        doc = None
        try:
            doc = scanning.get_file(scan_file)
        except FileNotFoundError:
            QtWidgets.QMessageBox.warning(self.ScanButton, "Error", "Unable to open file")
            return
        
        num_pages = scanning.get_number_of_pages(doc)
        logging.info(f"Number of pages in document: {num_pages}")

        self.OutputTextArea.append("Scan complete")
        student_answer_df = pd.DataFrame(columns=["Matriculation number","Question","Answer"]) #stores student answers

        unknown_matriculation_number = 99999999 #unknown matriculation number counter

        #read the answers from the scanned image noting any issues.    
        for i in range(num_pages):
            image = scanning.get_image_from_file(doc,i)
            df,image = scanning.read_image_answers(image,one_answer_only=one_answer_only,num_questions=num_questions,mark_image=True if pdf is not None else False)

            if pdf is not None:
                scanning.add_image_to_pdf(pdf,image)

            if df["Matriculation number"].values[0] == "99999999":
                logging.warning("Unable to read matriculation number on page "+str(i)+". Assigning matriculation number "+str(unknown_matriculation_number))
                df["Matriculation number"] = unknown_matriculation_number
                unknown_matriculation_number -= 1
            else:
                logging.info("Read matriculation number "+str(df["Matriculation number"].values[0])+" on page "+str(i))
            if df["Matriculation number"].values[0] == "00000000": #if the matriculation number is 0000000 then it is the model answers and we can work out how many questions there are.
                for i in range(1,121):
                    #check the content of df["Question","Answer",] os not empty
                    if len(df[df["Question"]==i]["Answer"].values[0]) == 0:
                        break
                num_questions = i-1
        
            if df["Matriculation number"].values[0] in student_answer_df["Matriculation number"].values:
                logging.warning("Duplicate matriculation number "+str(df["Matriculation number"].values[0])+" on page "+str(i)+"setting to"+str(unknown_matriculation_number))
                df["Matriculation number"] = unknown_matriculation_number
                unknown_matriculation_number -= 1 
            
            student_answer_df = pd.concat([df,student_answer_df],ignore_index=True)
        
        if answer_in_file: #if the answers ARE NOT in a separate file then we read them now
            answers_df = dataframes.read_answers_from_df(student_answer_df)
        
        if answers_df.empty:
            QtWidgets.QMessageBox.warning(self.ScanButton, "Error", "Unable to read answer file")
        
        student_answer_df = dataframes.compute_marks(student_answer_df,answers_df)
        output_df = dataframes.make_output_df(student_answer_df,answers_df)

        output_df.to_csv(output_file,index=False)
        logging.info(f"Output file written to {output_file}")

        if pdf is not None:
            scanning.save_pdf(pdf,self.ImageFileName.text())
        
        

def main():
    app = QtWidgets.QApplication(sys.argv)
    MainWindow = QtWidgets.QMainWindow()
    ui = AppMainWindow(MainWindow)
    MainWindow.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
    

