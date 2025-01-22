import cv2
import pypdfium2 as pdfium
import matplotlib.pyplot as plt
import numpy as np

FILE = "example.pdf"

doc = pdfium.PdfDocument(FILE)
page = doc[0]
print("Page size:", doc.get_page_size(0))

image = page.render(scale = 5,no_smoothimage=True,optimize_mode="print")
image = image.to_numpy()

#read the FILE as a tiff image
#image = cv2.imread(FILE)
image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
#show the image using matplotlib in its original size

plt.imshow(image)
plt.show()
