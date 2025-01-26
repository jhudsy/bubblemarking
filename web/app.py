from flask import Flask, request, redirect, url_for, flash, render_template,secure_filename
from flask_executor import Executor
from flask_mail import Mail, Message
from subprocess import run
import secrets
import os
from forms import ScanForm

UPLOAD_FOLDER = "uploads"
SCAN_SCRIPT = "bubblemarking"

app = Flask(__name__)
executor = Executor(app)
mail = Mail(app)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["SECRET_KEY"] = secrets.token_hex(16)
app.config["MAIL_SERVER"] = "smtp.gmail.com"
app.config["MAIL_PORT"] = 465
app.config["MAIL_USE_SSL"] = True
app.config["MAIL_USERNAME"] = ""
app.config["MAIL_PASSWORD"] = ""

app.config["SCAN_SCRIPT"] = SCAN_SCRIPT


def run_scan(scan_file,answer_file,email,one_answer_only):
    #run python3 scan.py scan_file output_file --one_answer_only --answer_file=answer_file (if answer_file is not none). Output_file is a randomly generated file name as is results_file

    #generate a secure random file name for the output file
    output_file = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename("output.csv"))
    

    params = []
    if one_answer_only:
        params.append("--one_answer_only")
    if answer_file:
        params.append(f"--answer_file={answer_file}")

    p = run(["python3","-m", app.config["SCAN_SCRIPT"], scan_file, output_file, *params], capture_output=True)
    p.wait()

    #send email
    msg = Message("Scan Results", sender=f"{email}", recipients=[email])
    msg.body = f"{p.stdout.decode()}"
    with app.open_resource(output_file) as fp:
        msg.attach("results.csv", "text/csv", fp.read())
    mail.send(msg)

    #clean up
    run(["rm", output_file])
    run(["rm", scan_file])
    if answer_file:
        run(["rm", answer_file])

@app.route("/scan", methods=["POST"])
def scan():
    form = ScanForm()
    if form.validate_on_submit():
        scan_file = request.files["scan_file"]
        answer_file = request.files.get("answer_file")
        email = request.form["email"]
        one_answer_only = request.form.get("one_answer_only", False)

        scan_file.filename = secure_filename(scan_file.filename)
        scan_file.save(os.path.join(app.config["UPLOAD_FOLDER"], scan_file.filename))
        if answer_file:
            answer_file.filename = secure_filename(answer_file.filename)
            answer_file.save(os.path.join(app.config["UPLOAD_FOLDER"], answer_file.filename))

        executor.submit(run_scan, scan_file.filename, answer_file.filename if answer_file else None, email, one_answer_only)
        flash("Scan started. Results will be emailed to you.")
        #redirect to same page
        return redirect(url_for("scan"))
    return render_template("scan.html", form=form)
    


    
    


