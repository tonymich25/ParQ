import os
from dotenv import load_dotenv
from flask import Flask
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
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

class User(db.Model, UserMixin):


class Log(db.Model):


class Booking(db.Model, UserMixin):



class City(db.Model, UserMixin):


class ParkingLot(db.Model, UserMixin):


from accounts.views import accounts_bp
from dashboard.views import dashboard_bp
from booking.views import booking_bp
app.register_blueprint(accounts_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(booking_bp)