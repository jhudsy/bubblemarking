This program reads a University of Aberdeen MCQ marksheet and outputs the results. It can be run via the command line, a GUI or the web.

N.B., before starting, `PYTHONPATH` should be set to the directory containing `bubblemarking`.

Command line
============

`python3 -m bubblemarking` will give relevant instructions.
Ditto for `python3 bubblemarking/main.py`

GUI
===

`python3 -m bubblemarking.gui` will run the GUI as will `python3 bubblemarking/gui/main.py`

The GUI was prepared using Qt6Designer. If any changes are made, you will need to run `pyside6-uic -o gui.py gui.ui`; the designer outputs its results in gui.ui and this creates a gui.py file which main inherits from.

Web
===
A flask interface is available under the web subdirectory.

File Format
===========
The answer file format is a CSV or XLSX file with no headers. Each row contains a question number and the comma separated answers. E.g.,

```
1,"A,B,E"
2,"A"
...
```

If answers are in the scans, they should appear with matriculation number 00000000

