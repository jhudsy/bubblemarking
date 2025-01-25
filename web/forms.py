from flask_wtf import FlaskForm
from wtfforms import SubmitField, BooleanField,TextAreaField,validators
from flask_wtf.file iport FileField, FileRequired

class UploadForm(FlaskForm):
  scan_file = FileField('Scan File', validators = [FileRequired()])
  email_address = TextAreaField('Email for results', validators = [validators.InputRequired(),validators.Email(check_deliverable=True)])
  one_answer_only = BooleanField('One answer per question only')
  answer_file = FileField('Answers File, leave blank if answers are in the scans.')
  submit = SubmitField('Submit')
