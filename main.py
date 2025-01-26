import logging
import argparse
import pandas as pd
from bubblemarking.scanning import get_file, get_number_of_pages, get_image_from_file, read_image_answers
from bubblemarking.dataframes import read_answers_from_file, read_answers_from_df, compute_marks, make_output_df

def main():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logging.basicConfig(format='"%(levelname)s - %(message)s')
    #argument are filename and output_filename. Additional arguments are --read_answers_from_file=<FILENAME> 

    parser = argparse.ArgumentParser(description='Detects multiple choice answers from a scanned image of a multiple choice exam.')
    parser.add_argument('filename', type=str, help='The filename of the scanned exam.')
    parser.add_argument('output_filename', type=str, help='The filename of the output file.')
    parser.add_argument('--read_answers_from_file', help='The filename of the file containing the answers. If not present, answers should be read from a scanned image. with matriculation number 0000000',default=None)
    parser.add_argument("--one_answer_only",help="allow only one answer per questions",default=False)

    args = parser.parse_args()
    FILE = args.filename
    OUTPUT_FILE = args.output_filename
    READ_ANSWERS_FROM_FILE = args.read_answers_from_file
    ONE_ANSWER_ONLY = args.one_answer_only

    doc = get_file(FILE)
    num_pages = get_number_of_pages(doc)
    logging.info(f"Number of pages in document: {num_pages}")

    student_answer_df = pd.DataFrame(columns=["Matriculation number","Question","Answer"]) #stores student answers

    unknown_matriculation_number = 99999999 #unknown matriculation number counter

    #read the answers from the scanned image noting any issues.    
    for i in range(num_pages):
        df = read_image_answers(get_image_from_file(doc,i),ONE_ANSWER_ONLY=ONE_ANSWER_ONLY)
        if df["Matriculation number"].values[0] == "99999999":
            logging.warning(f"Unable to read matriculation number on page {i}. Assigning matriculation number {unknown_matriculation_number}")
            df["Matriculation number"] = unknown_matriculation_number
            unknown_matriculation_number -= 1
        else:
            logging.info(f"Read matriculation number {df['Matriculation number'].values[0]} on page {i}")
        
        #check if matriculation number already exists in student_answer_df
        if df["Matriculation number"].values[0] in student_answer_df["Matriculation number"].values:
            logging.warning(f"Duplicate matriculation number {df['Matriculation number'].values[0]} on page {i} setting to {unknown_matriculation_number}")
            df["Matriculation number"] = unknown_matriculation_number
            unknown_matriculation_number -= 1
        
        student_answer_df = pd.concat([student_answer_df,df],ignore_index=True)
                
    #read the answers from the answer file or from student_answer_df        
    answers_df = None
    if READ_ANSWERS_FROM_FILE is not None:
        answers_df = read_answers_from_file(READ_ANSWERS_FROM_FILE)
    else:
        answers_df = read_answers_from_df(student_answer_df)
        
    #compute marks
    student_answer_df = compute_marks(student_answer_df,answers_df)
    output_df = make_output_df(student_answer_df,answers_df)
    
    output_df.to_csv(OUTPUT_FILE,index=False)

if __name__=="__main__":
    main()

    
