from flask import Flask
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

def create_app():
    from .blueprints.browser_bp import browser_bp

    app = Flask(__name__)
    app.config.from_object("config.Config")
    db.init_app(app)

    app.register_blueprint(browser_bp, url_prefix="/export")

    with app.app_context():
        db.create_all()

    return app
