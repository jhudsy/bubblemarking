import pandas as pd
import logging
import sys

def read_answers_from_file(filename):
    try:
        answers_df = pd.read_csv(filename,header=None,names=["Question","Answer"])
    except:
        answers_df = pd.read_excel(filename,header=None,names=["Question","Answer"])
    return answers_df
###############################################################################
def read_answers_from_df(df,**kwargs):
    matriculation_number = kwargs.get("matriculation_number", "0000000")
    answers_df = df[df["Matriculation number"]==matriculation_number]
    answers_df = answers_df.drop(columns=["Matriculation number"])
    #remove the row with matriculation number 0000000 from student_answer_df
    df = df[df["Matriculation number"]!=matriculation_number]
    if len(answers_df) == 0:
        logging.fatal("No answer sheet found in scans")
        #sys.exit(1)
    return answers_df
###############################################################################
def  compute_mark(answer,correct_answer):
    """returns a triple (num_correct,num_incorrect) where num_correct is the number of correct answers and num_incorrect is the number of incorrect answers given"""
    num_correct = 0
    num_incorrect = 0
    #answer and correct_answer are strings of the form "A,B,C"
    if len(answer) == 0: #handle the case where the student has not answered the question
        return 0,0
    
    answer = answer.split(",")
    correct_answer = correct_answer.split(",")

    for a in answer:        
        if a in correct_answer:
            num_correct += 1
        else:
            num_incorrect += 1

    return num_correct,num_incorrect

###############################################################################
def compute_marks(student_answer_df,answers_df):
    for i in range(len(student_answer_df)):
        question = student_answer_df.iloc[i]["Question"]
        if question not in answers_df["Question"].values:
            logging.warning(f"Question {question} not in answer sheet")
            continue
        answer = student_answer_df.iloc[i]["Answer"]
        correct_answer = answers_df[answers_df["Question"]==question]["Answer"].values[0]
        num_correct,num_incorrect = compute_mark(answer,correct_answer)

        student_answer_df.at[i,"Correct"] = num_correct
        student_answer_df.at[i,"Incorrect"] = num_incorrect
    return student_answer_df
###############################################################################
def make_output_df(student_answer_df,answers_df):
    #create an output df with the columns Matriculation number, Question1, ..., QuestionN where N is the number of questions. The first row will have matriculation number 0000000 and the total number of correct answers for each question. E.g., if question 3 had 5 correct answers, the cell for question 3 will contain 5. We also have Question1Answer, ..., QuestionNAnswer where the first row will contain the correct answers for each question. E.g., if question 3 had answers A,B,C by the student the cell for Question3Answer will contain "A,B,C"
    output_df = pd.DataFrame(columns=["Matriculation number"])
    output_df["Matriculation number"] = ["0000000"]
    #compute total number of questions by looking at answers
    total_questions = len(answers_df)
    #add columns for each question and the number of correct answers and the correct answers using the answer_df dataframe
    for i in range(1,total_questions+1):
        output_df["Question"+str(i)+"NumCorrect"] = len(answers_df[answers_df["Question"]==i]["Answer"].values[0].split(","))
        output_df["Question"+str(i)+"NumIncorrect"] = 0
        output_df["Question"+str(i)+"Answer"] = answers_df[answers_df["Question"]==i]["Answer"].values[0]

    #now fill in the student answers into output_df
    for i in range(len(student_answer_df)):
        matriculation_number = student_answer_df.iloc[i]["Matriculation number"]
        question = student_answer_df.iloc[i]["Question"] #question number
        answer = student_answer_df.iloc[i]["Answer"] #answer string, e.g., "A,B,C"
        num_correct = student_answer_df.iloc[i]["Correct"] #number of correct answers
        num_incorrect = student_answer_df.iloc[i]["Incorrect"] #number of incorrect answers
           
        if matriculation_number not in output_df["Matriculation number"].values:
            output_df = pd.concat([output_df,
                                   pd.DataFrame({"Matriculation number":[matriculation_number]})],ignore_index=True)

        if question<total_questions+1: #only add the answer if it is a valid question
            
            index = output_df[output_df["Matriculation number"]==matriculation_number].index[0]

            output_df.at[index,"Question"+str(question)+"NumCorrect"] = num_correct
            output_df.at[index,"Question"+str(question)+"NumIncorrect"] = num_incorrect
            output_df.at[index,"Question"+str(question)+"Answer"] = answer

    return output_df
