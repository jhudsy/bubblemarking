import cv2
import pypdfium2 as pdfium
import matplotlib.pyplot as plt
import numpy as np
import sys
import argparse

import scipy.signal
import pandas as pd

from copy import deepcopy

import logging

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
def find_right(line):
    #used in offset calculations in get_mark_indexes function
    line = cv2.cvtColor(line, cv2.COLOR_BGR2GRAY)
    line = cv2.threshold(line, 200, 255, cv2.THRESH_BINARY)[1]
    line = cv2.erode(line,np.ones([3,3]),iterations=2)
    #invert image
    line = cv2.bitwise_not(line)
    count = 0
    for i in range(line.shape[1]-1,0,-1):
        if np.sum(line[5:25,i]) > 4000:
            count += 1
        elif count<60: #N.B. Constant here
            count = 0
        else: #we have found a white cell and count is more than 60
            #plt.imshow(line[:,i:],cmap="gray")
            #plt.show()
            return i
    logging.critical("Error, black bar not found")
    return None
###############################################################################
def get_question_answers(image,question_number,bars,right_bar_cache,**kwargs):
    #image is the image
    #question_number is the question number
    #bars is the black bars
    #right_bar_cache is a cache of the right bar for each question
    #additional arguments:
    #window_size is the size of the window to search for marks
    #window_height is the height of the window to search for marks
    #threshold is the threshold for deciding whether a mark is filled or not, expressed as a percentage of the maximum
    #red_threshold is the threshold for the red channel
    #mark_image is a boolean indicating whether to write a mark on the image showing where the mark was found
    #Returns an array of answers for the question and an updated right_bar_cache as needed.
    #We assume that at least one of the answers is not filled in.
    offsets = [[-2397,-2325,-2252,-2180,-2108],
               [-1819,-1747,-1674,-1602,-1530],
               [-1241,-1169,-1096,-1024,-951],
               [-663,-590,-519,-446,-374]]
    
    window_height = kwargs.get("window_height", 1)
    window_size = kwargs.get("window_size", 58)
    threshold = kwargs.get("threshold", 0.75)
    red_threshold = kwargs.get("red_threshold", 170)
    one_answer_only = kwargs.get("one_answer_only", False)
    mark_image = kwargs.get("mark_image", False)

    #the column we need is question_number//30 and the bar we need is 12+question_number%30
    line = image[bars[question_number%30+12][0]:bars[question_number%30+12][1],:]
    if right_bar_cache.get(question_number%30+12,None) is None:
        right_bar_cache[question_number%30+12] = find_right(line)
    right = right_bar_cache[question_number%30+12]

    offset = offsets[question_number//30]

    brightness_array = np.zeros([len(offset)])
    for i in range(len(offset)):
        window = line[int((1-window_height)*line.shape[0]):int(window_height*line.shape[0]),right+offset[i]-window_size//2:right+offset[i]+window_size//2].copy()
        window = window[:,:,0] #take only the red channel
        window = cv2.threshold(window, red_threshold, 255, cv2.THRESH_BINARY)[1]
        window=cv2.erode(window,np.ones([3,3]),iterations=2)
        brightness_array[i] = np.sum(window)
    
    #print(question_number,brightness_array)

    #if an element in the brhightness array is 10% less than the maximum, we assume it is filled in
    answers = []
    for i in range(len(brightness_array)):
        if brightness_array[i] < threshold*np.max(brightness_array):
            answers.append(i)

    if one_answer_only:
        #the answer is the index of the least bright element
        ans = np.argmin(brightness_array)
        
        if len(answers) > 1:
            logging.warning(f"Student has selected more than one answer ({answers}) for question {question_number+1}")
            #logging.warning(f"{brightness_array/np.max(brightness_array)}")
        if brightness_array[ans] > 0.9*np.max(brightness_array): #N.B. Constant here
            answers = []
        else:      
            answers = [int(ans)]
    
    if mark_image:
        for i in range(len(offset)):
            if i in answers:
                cv2.rectangle(line,(right+offset[i]-window_size//2,bars[question_number%30+12][0]+int((1-window_height)*line.shape[0])),(right+offset[i]+window_size//2,bars[question_number%30+12][0]+int(window_height*line.shape[0])),(0,0,255),1)
        
    return answers,right_bar_cache

###############################################################################
def get_matriculation_number(image,bars,**kwargs):
    offsets = [-594, -522, -449, -377, -305, -233, -161, -89]
    window_height = kwargs.get("window_height", 0.8)
    window_size = kwargs.get("window_size", 60)
    red_threshold = kwargs.get("red_threshold", 200)
    brightness_matrix = np.zeros([10,len(offsets)])
    mark_image = kwargs.get("mark_image", False)

    for i in range(2,12):
        line = image[bars[i][0]:bars[i][1],:]
        right = find_right(line)
        for j in range(len(offsets)):
            window = line[int((1-window_height)*line.shape[0]):int(window_height*line.shape[0]),right+offsets[j]-window_size//2:right+offsets[j]+window_size//2].copy()
            window = window[:,:,0]
            window = cv2.threshold(window, red_threshold, 255, cv2.THRESH_BINARY)[1]
            brightness_matrix[i-2,j] = np.sum(window)
    
    matriculation_number = 0
    #iterate through each column finding the minimum
    for j in range(len(offsets)):
        min_index = np.argmin(brightness_matrix[:,j])
        matriculation_number += (min_index)*10**(7-j)
        #plt.imshow(image[bars[min_index+2][0]:bars[min_index+2][1],right+offsets[j]-window_size//2:right+offsets[j]+window_size//2])
        #plt.show()

    if mark_image:
        for i in range(2,12):
            line = image[bars[i][0]:bars[i][1],:]
            right = find_right(line)
            for j in range(len(offsets)):
                if brightness_matrix[i-2,j] == np.min(brightness_matrix[:,j]):
                    cv2.rectangle(line,(right+offsets[j]-window_size//2,bars[i][0]+int((1-window_height)*line.shape[0])),(right+offsets[j]+window_size//2,bars[i][0]+int(window_height*line.shape[0])),(0,0,255),1)
    return str(matriculation_number).zfill(8)


###############################################################################
 
def get_all_answers(image,bars,**kwargs):
    num_questions = kwargs.get("num_questions",120)
    if num_questions == None or num_questions > 120 or num_questions < 1:
        num_questions = 120

    answer_map = {}
    right_bar_cache = {}
    for i in range(num_questions):
        answer_map[i+1] = get_question_answers(image,i,bars,right_bar_cache,**kwargs)
    
    #work backwards removing any elements from answermap for which the answer is [] until we reach an element for which the answer is not []
    for i in range(num_questions-1,-1,-1):
        if len(answer_map[i+1]) == 0:
            del answer_map[i+1]
        else:
            break
       
    logging.debug(f"Answer map: {answer_map}")
    
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
def read_image_answers(image,**kwargs):
    one_answer_only = kwargs.get("one_answer_only",False)
    num_questions = kwargs.get("num_questions",120)
    df = pd.DataFrame(columns=["Matriculation number","Question","Answer"]) #stores student answers
    prepared_image, blackBars = prepare_image(image)
    if prepared_image is None:
        logging.fatal(f"Unable to read page")
        sys.exit(1)
    ans = get_all_answers(prepared_image,blackBars,one_answer_only = one_answer_only,num_questions=num_questions)

    matriculation_number = get_matriculation_number(prepared_image,blackBars)
    
    if matriculation_number is None:
            matriculation_number = "99999999"

    for j in ans:
        if one_answer_only and len(ans[j])>1:
            logging.warning(f"Student {matriculation_number} has selected more than one answer for question {j}")
        df = pd.concat([df,pd.DataFrame({
            "Matriculation number":[matriculation_number],
                "Question":[j],
                "Answer":[answers_to_string(ans[j])]
            })],ignore_index=True)
    return df
###############################################################################



    
