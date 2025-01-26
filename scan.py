import cv2
import pypdfium2 as pdfium
#import matplotlib.pyplot as plt
import numpy as np
import sys
import argparse

import scipy.signal
import pandas as pd

import logging

logger = logging.getLogger(__name__)

###############################################################################
def get_file(file_name):
    return pdfium.PdfDocument(file_name)

def get_number_of_pages(doc):
    return len(doc)
    
def get_image_from_file(doc,page_number,**kwargs):
    SCALE = kwargs.get("SCALE", 5.0)
    page = doc[page_number]

    image = page.render(scale = SCALE,no_smoothimage=True,optimize_mode="print")
    image = image.to_numpy()

    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    return image

###############################################################################
def straighten_image(original_image,**kwargs):
    threshold = kwargs.get("threshold", 40)
    image_percent = kwargs.get("image_percent", 0.05)
    image = cv2.cvtColor(original_image, cv2.COLOR_BGR2GRAY)
    height = image.shape[0]

    _, thresh = cv2.threshold(image, threshold, 255, cv2.THRESH_BINARY)
    thresh = cv2.bitwise_not(thresh)
    linesTop = cv2.HoughLinesP(thresh[0:int(height*image_percent)],1, np.pi/180, 100, minLineLength=5, maxLineGap=100) #N.B., 5% here
    linesBottom = cv2.HoughLinesP(thresh[int(height-height*image_percent):],1, np.pi/180, 100, minLineLength=20, maxLineGap=100)
    
    lines = np.concatenate((linesTop, linesBottom))
    angles = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        angle = np.arctan2(y2 - y1, x2 - x1) * 180 / np.pi
        angles.append(angle)

    angle = np.mean(angles)
    
    image = cv2.warpAffine(original_image, cv2.getRotationMatrix2D((image.shape[1]//2, image.shape[0]//2), angle, 1), (image.shape[1], image.shape[0]))
    return image
###############################################################################
def find_black_bars(orig_image, **kwargs):
    
    threshold = kwargs.get("threshold", 127)
    right_scan_percent = kwargs.get("right_scan_percent", 0.03) 
    num_black_Bars = kwargs.get("num_black_Bars", 44)
    width = orig_image.shape[1]

    image = cv2.cvtColor(orig_image, cv2.COLOR_BGR2GRAY)

    _, thresh = cv2.threshold(image, threshold, 255, cv2.THRESH_BINARY)

    blackBars = []
    foundTop = False
    for i in range(0,thresh.shape[0]):
        if thresh[i,int(-width*right_scan_percent)] == 0 and not foundTop: #N.B., 3% here
            foundTop = True
            top = i
        if thresh[i,int(-width*right_scan_percent)] == 255 and foundTop:
            foundTop = False
            blackBars.append((top, i))
    
    if len(blackBars) != num_black_Bars: 
        return None
    return blackBars
###############################################################################
def prepare_image(image, **kwargs):
    if image.shape[0] < image.shape[1]: #if it's been loaded sideways
        image = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    new_image = straighten_image(image, **kwargs)
    blackBars = find_black_bars(new_image, **kwargs)
    if blackBars == None:
        new_image = cv2.rotate(new_image, cv2.ROTATE_180)
        blackBars = find_black_bars(new_image, **kwargs)
    if blackBars == None:
        return None,None
    return new_image, blackBars  
###############################################################################
def get_mark_indexes(line,area=None,**kwargs):
    answers = []
    num_dilations = kwargs.get("num_dilations", 2)
    h_thresh = kwargs.get("h_thresh", 90)
    s_thresh = kwargs.get("s_thresh", 90)
    v_thresh = kwargs.get("v_thresh", 200)    
    window_width = kwargs.get("window_width", 60) #the width of the letters on the answer sheet
    #line is a color image

    line = line[:,int(line.shape[1]*area[0]):int(line.shape[1]*area[1])]
    line = cv2.cvtColor(line, cv2.COLOR_BGR2HSV)
    h = line[:,:,0]
    s = line[:,:,1]
    v = line[:,:,2]
    _,h = cv2.threshold(h, h_thresh, 255, cv2.THRESH_BINARY)
    _,s = cv2.threshold(s, s_thresh, 255, cv2.THRESH_BINARY)
    _,v = cv2.threshold(v, v_thresh, 255, cv2.THRESH_BINARY)

    mark_mask = np.logical_and(h == 255, v == 0)
    mark_mask_location = np.where(mark_mask, 255, 0)
    
    mark_mask_location = mark_mask_location.astype(np.uint8)
    #dilate the mask location
    
    mark_mask_location = cv2.dilate(mark_mask_location, np.ones((3,3),np.uint8), iterations=num_dilations)

    #we now introduce a new mask on the h and s channels
    hsmask = np.logical_and(h == 255, s == 255)
    hsmask_location = np.where(hsmask, 255, 0)
    hsmask_location = hsmask_location.astype(np.uint8) 
    #dilate hsmask_location
    hsmask_location = cv2.dilate(hsmask_location, np.ones((3,3),np.uint8), iterations=num_dilations)

    #the mark_mask_location will have a strong white area where the student has marked the answer
    #the hsmask_location will have a strong white area where the answer letters are.

    #try add blur to both
    mark_mask_location = cv2.GaussianBlur(mark_mask_location,(7,7),0)
    hsmask_location = cv2.GaussianBlur(hsmask_location,(7,7),0)
    
    mask_value = np.zeros([line.shape[1]-window_width]) #this is the mask for the hsmask_location containing the multiple choice letters
    ans_mask_value = np.zeros([line.shape[1]-window_width]) #this is the mask for the mark_mask_location containing the student answers

    #count number of white pixels in the window for the two masks and fill them in.
    for x in range(line.shape[1]-window_width//2,window_width//2,-1):
        window = hsmask_location[:,x-window_width//2:x+window_width//2]
        mask_value[x-1-window_width//2] = np.sum(window)

        window = mark_mask_location[:,x-window_width//2:x+window_width//2]
        ans_mask_value[x-1-window_width//2] = np.sum(window)
    
    answer_peaks = scipy.signal.find_peaks(ans_mask_value,distance=50,height=200000)
    two_peaks = scipy.signal.find_peaks(mask_value,distance=50,height=100000)
    
    for i in range(len(two_peaks[0])):    
        closest_distance = 100000
        for j in range(len(answer_peaks[0])):
            distance = abs(two_peaks[0][i]-answer_peaks[0][j])
            if distance < closest_distance:
                closest_distance = distance
    
        if closest_distance < 10:
            answers.append(i)
        else:
            continue
        #add the answers to the answer list as a tuple containing the index of True answers 
    return answers

###############################################################################    
def get_matriculation_number(image,bars,**kwargs):
    matriculation_number = 0
    digits = []
    area = kwargs.get("matriculation_number_area", (0.75,0.96))
    for i in range(2,12):
        line = image[bars[i][0]:bars[i][1],:].copy()
        answers = get_mark_indexes(line,area=area,**kwargs)
        for a in answers:
            matriculation_number += (i-2)*10**(7-a)
            digits.append(a)
    #turn matriculation number into a 8 digit string with 0 padding as needed
    for i in range(8):
        #if i does not appear in digits or it appears more than once, return None as an error
        if i not in digits or digits.count(i) > 1:
            return None
    return str(matriculation_number).zfill(8)
###############################################################################
def get_answers(line,**kwargs):
    answer_bounds = kwargs.get("answer_bounds", [[0.14,0.29],[0.33,0.478],[0.525,0.675],[0.72,0.87]])

    answers = []
    for a in answer_bounds:
        answers.append(get_mark_indexes(line,area=a,**kwargs))

    return answers
###############################################################################
def get_all_answers(image,bars,**kwargs):
    answer_map = {}
    
    for i in range(12,42):
        line = image[bars[i][0]:bars[i][1],:].copy()
        answers = get_answers(line, **kwargs)
        #answers are for questions (i-11),(i-11)+30,(i-11)+60 and (i-11)+90
        answer_map[i-11] = answers[0]
        answer_map[i-11+30] = answers[1]
        answer_map[i-11+60] = answers[2]
        answer_map[i-11+90] = answers[3]
    
    return answer_map
###############################################################################
def answers_to_string(answers):
    #answers is an array containing e.g. [0,2,4] which means the student has selected answers A,C,E
    #we return a string "A,C,E"
    if len(answers) == 0:
        return ""
    answer_string = ""
    for a in answers:
        answer_string += chr(65+a)+","
    return answer_string[:-1]
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
def read_image_answers(image,**kwargs):
    ONE_ANSWER_ONLY = kwargs.get("ONE_ANSWER_ONLY",False)
    df = pd.DataFrame(columns=["Matriculation number","Question","Answer"]) #stores student answers
    prepared_image, blackBars = prepare_image(image)
    if prepared_image is None:
        logger.fatal(f"Unable to read page {i}")
        sys.exit(1)
    ans = get_all_answers(prepared_image,blackBars)

    matriculation_number = get_matriculation_number(prepared_image,blackBars)
    
    if matriculation_number is None:
            matriculation_number = "99999999"

    for j in ans:
        if ONE_ANSWER_ONLY and len(ans[j])>1:
            logger.warning(f"Student {matriculation_number} has selected more than one answer for question {j}")
        df = pd.concat([df,pd.DataFrame({
            "Matriculation number":[matriculation_number],
                "Question":[j],
                "Answer":[ans[j]]
            })],ignore_index=True)
    return df
###############################################################################
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
    df = df[student_answer_df["Matriculation number"]!=matriculation_number]
    if len(answers_df) == 0:
        logger.fatal("No answer sheet found in scans")
        sys.exit(1)
    return answers_df
###############################################################################
def compute_marks(student_answer_df,answers_df):
    for i in range(len(student_answer_df)):
        question = student_answer_df.iloc[i]["Question"]
        if question not in answers_df["Question"].values:
            logger.warning(f"Question {question} not in answer sheet")
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
###############################################################################

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
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
    logger.info(f"Number of pages in document: {num_pages}")

    student_answer_df = pd.DataFrame(columns=["Matriculation number","Question","Answer"]) #stores student answers

    unknown_matriculation_number = 99999999 #unknown matriculation number counter

    #read the answers from the scanned image noting any issues.    
    for i in range(num_pages):
        df = read_image_answers(get_image_from_file(doc,i),ONE_ANSWER_ONLY=ONE_ANSWER_ONLY)
        if df["Matriculation number"].values[0] == "99999999":
            logger.warning(f"Unable to read matriculation number on page {i}. Assigning matriculation number {unknown_matriculation_number}")
            df["Matriculation number"] = unknown_matriculation_number
            unknown_matriculation_number -= 1
        else:
            logger.info(f"Read matriculation number {df['Matriculation number'].values[0]} on page {i}")
        
        #check if matriculation number already exists in student_answer_df
        if df["Matriculation number"].values[0] in student_answer_df["Matriculation number"].values:
            logger.warning(f"Duplicate matriculation number {df['Matriculation number'].values[0]} on page {i} setting to {unknown_matriculation_number}")
            df["Matriculation number"] = unknown_matriculation_number
            unknown_matriculation_number -= 1 
            
        pd.concat([student_answer_df,df],ignore_index=True)
                
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



    
