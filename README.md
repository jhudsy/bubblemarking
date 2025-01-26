This program reads a University of Aberdeen MCQ marksheet and outputs the results. It can be run via the command line, a GUI or the web.

Command line
============

python3 scan.py will give relevant instructions.

GUI
===

python3 main.py will run the GUI.

The GUI was prepared using Qt6Designer. If any changes are made, you will need to run `pyuic6 -o gui.py gui.ui`; the designer outputs its results in gui.ui and this creates a gui.py file which main inherits from.

Web
===
A flask interface is available under the web subdirectory.

File Format
===========
The answer file format is a CSV or XLSX file with no headers. Each row contains a question number and the comma separated answers. E.g.,

1,"A,B,E"
2,"A"
...

If answers are in the scans, they should appear with matriculation number 00000000

