from datetime import datetime
import os
from dotenv import load_dotenv
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, LoginManager
from flask_migrate import Migrate
from sqlalchemy import MetaData

app = Flask(__name__)

# LOAD .ENV FILE
load_dotenv()


# DATABASE CONFIGURATION
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('SQLALCHEMY_DATABASE_URI')
app.config['SQLALCHEMY_ECHO'] = True if os.getenv('SQLALCHEMY_ECHO') == 'True' else False
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = True if os.getenv('SQLALCHEMY_TRACK_MODIFICATIONS') == 'True' else False


metadata = MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_names)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s"
    }
)


db = SQLAlchemy(app, metadata=metadata)
migrate = Migrate(app, db)

