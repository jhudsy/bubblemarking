import cv2
import pypdfium2 as pdfium
#import matplotlib.pyplot as plt
import numpy as np
import sys
import argparse

import scipy.signal
import pandas as pd

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
    for i in range(line.shape[1]-1,0,-1):
        if np.sum(line[5:25,i]) > 0 and np.sum(line[:,i-1]) == 0:
            return i
###############################################################################

def get_mark_indexes(line,area=None,**kwargs):
    #area is an offset of start and end point of the line to search for marks. Assume area[0]<area[1]<=0
    #additional arguments:
    #offsets is an array of offsets to search for marks, they should all be negative
    #threshold is the threshold for deciding whether a mark is filled or not
    #distance_from_threshold is the allowable distance from offset where a mark can appear
    #mask width is the width of the mask to search for marks

    offsets = kwargs.get("offsets", None)
    threshold = kwargs.get("threshold", 200000) #given a 60x30 mask we want at least 65% of the pixels to be black
    distance_from_threshold = kwargs.get("distance_from_threshold", 10)
    mask_width = kwargs.get("mask_width", 60)
    peak_distance = kwargs.get("peak_distance", 50)
    peak_prominence = kwargs.get("peak_prominence", 10000)

    line = line.copy()
    
    #if we are using the offset method, compute the right origin from which absolute coordinates will be computed
    offset_points = []
    if offsets is not None:
       right = find_right(line)
       for o in offsets:
              #offsets should be less than 0
              assert(o < 0)
              offset_points.append(right+o)
    
    #now we use the masking method to search the area and get points too
    line = cv2.cvtColor(line, cv2.COLOR_BGR2GRAY)
    line = cv2.threshold(line, 200, 255, cv2.THRESH_BINARY)[1]
    #invert image
    line = cv2.bitwise_not(line)

    mask = np.ones([line.shape[0],mask_width])
    conv = np.zeros([line.shape[1]-mask_width])
    for i in range(conv.shape[0]):
        window = line[:,i:i+mask_width]
        conv[i] = np.sum(window*mask)
    
    peak_points = scipy.signal.find_peaks(conv,
                                    distance=peak_distance,prominence=peak_prominence)[0]
    
    #filter out peak points not in area
    if area is not None:
        assert(area[0] < area[1])
        assert(area[1]< 0 )
        #area is an offset from the right, turn it into an absolute coordinates
        area[0] = right + area[0]
        area[1] = right + area[1]
        peak_points = [p for p in peak_points if p > area[0]*line.shape[1] and p < area[1]*line.shape[1]]

    #we go through peak_points looking whether they are within distance_from_threshold of any offset_points and we keep them. If there are any offset_points remaining we add them to the list
    point_list = []
    for p in peak_points:
        if len(offset_points) == 0: #handle case where no offset points were given
            point_list.append(p)
            continue
        for o in offset_points:
            if abs(p-o) < distance_from_threshold:
                point_list.append(p)
                offset_points.remove(o)
                break
    for o in offset_points:
        point_list.append(o)

    answers = []
    for p in point_list:
        if np.sum(line[:,p-mask_width//2:p+mask_width//2]) > threshold:
            answers.append(p)
    return answers
    
###############################################################################    
def get_matriculation_number(image,bars,**kwargs):
    matriculation_number = 0
    digits = []
    area = kwargs.get("area", [-658,-32])
    offsets = kwargs.get("matriculation_number_offsets", [-594, -522, -449, -377, -305, -233, -161, -89])
    #add offsets to kwargs if not there
    if "offsets" not in kwargs:
        kwargs["offsets"] = offsets
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
    logging.debug("Matriculation number: "+str(matriculation_number))
    return str(matriculation_number).zfill(8)
###############################################################################
def get_answers(line,**kwargs):
    offsets = [[-2397,-2325,-2252,-2180,-2108],
               [-1819,-1747,-1674,-1602,-1530],
               [-1241,-1169,-1096,-1024,-951],
               [-663,-590,-519,-446,-374]]
    areas = [[-2457,-2048],[-1877,-1470],[-1301,-891],[-723,-314]]
    answer_bounds = kwargs.get("answer_bounds", [[0.14,0.29],[0.33,0.478],[0.525,0.675],[0.72,0.87]])

    answers = []
    for a in answer_bounds:
        answers.append(get_mark_indexes(line,area=a,offsets=offsets.pop(0),**kwargs))

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
    ONE_ANSWER_ONLY = kwargs.get("ONE_ANSWER_ONLY",False)
    df = pd.DataFrame(columns=["Matriculation number","Question","Answer"]) #stores student answers
    prepared_image, blackBars = prepare_image(image)
    if prepared_image is None:
        logging.fatal(f"Unable to read page")
        sys.exit(1)
    ans = get_all_answers(prepared_image,blackBars)

    matriculation_number = get_matriculation_number(prepared_image,blackBars)
    
    if matriculation_number is None:
            matriculation_number = "99999999"

    for j in ans:
        if ONE_ANSWER_ONLY and len(ans[j])>1:
            logging.warning(f"Student {matriculation_number} has selected more than one answer for question {j}")
        df = pd.concat([df,pd.DataFrame({
            "Matriculation number":[matriculation_number],
                "Question":[j],
                "Answer":[answers_to_string(ans[j])]
            })],ignore_index=True)
    return df
###############################################################################



    
