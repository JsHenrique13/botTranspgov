from . import db

class ExportRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    source_url = db.Column(db.String(512))
    year = db.Column(db.String(10))
    raw_json = db.Column(db.Text)
