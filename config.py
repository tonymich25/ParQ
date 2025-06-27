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

# SECRET KEY FOR FLASK FORMS
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
app.config['RECAPTCHA_PRIVATE_KEY'] = os.getenv('RECAPTCHA_PRIVATE_KEY')
app.config['RECAPTCHA_PUBLIC_KEY'] = os.getenv('RECAPTCHA_PUBLIC_KEY')

# Initialising Login Manager
login_manager = LoginManager()
login_manager.init_app(app)

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
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), nullable=False, unique=True)
    password = db.Column(db.String(100), nullable=False)
    firstname = db.Column(db.String(100), nullable=False)
    lastname = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(100), nullable=False)
    log = db.relationship("Log", uselist=False, back_populates="user")
    bookings = db.relationship('Booking', backref='user', lazy=True)

    def __init__(self, email, firstname, lastname, phone, password):
        self.email = email
        self.firstname = firstname
        self.lastname = lastname
        self.phone = phone
        self.password = password
        self.role = "end_user"

    @login_manager.user_loader
    def load_user(id):
        return User.query.get(int(id))

    def get_id(self):
        return int(self.id)

    def check_password(self, password):
        try:
            correct_password = passwordHasher.verify(self.password, password)
        except:
            correct_password = False
        return correct_password

    def generate_log(self):
        user_log = Log(self.id)
        self.log = user_log
        db.session.commit()


class Log(db.Model):
    __tablename__ = 'logs'
    id = db.Column(db.Integer, primary_key=True)
    userid = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    registration = db.Column(db.DateTime, nullable=False)
    latestlogin = db.Column(db.DateTime, nullable=True)
    previouslogin = db.Column(db.DateTime, nullable=True)
    latestIP = db.Column(db.String(100), nullable=True)
    previousIP = db.Column(db.String(100), nullable=True)
    user = db.relationship("User", back_populates="log")

    def __init__(self, userid):
        self.userid = userid
        self.registration = datetime.now()


class Booking(db.Model, UserMixin):
    __tablename__ = 'bookings'
    id = db.Column(db.Integer, primary_key=True)
    userid = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    spot_id = db.Column(db.Integer, db.ForeignKey('parking_spots.id'), nullable=False)
    timeBooked = db.Column(db.DateTime, default=datetime.now, nullable=False)
    startTime = db.Column(db.Time, nullable=False)
    endTime = db.Column(db.Time, nullable=False)

    # Relationships
    parking_spot = db.relationship('ParkingSpot', back_populates='bookings')

    def __init__(self, userid, spot_id, startTime, endTime):
        self.userid = userid
        self.spot_id = spot_id
        self.startTime = startTime
        self.endTime = endTime


class City(db.Model, UserMixin):
    __tablename__ = 'cities'
    id = db.Column(db.Integer, primary_key=True)
    city = db.Column(db.Text, nullable=False)


class ParkingLot(db.Model, UserMixin):
    __tablename__ = 'parking_lots'
    id = db.Column(db.Integer, primary_key=True)
    city_id = db.Column(db.Integer, db.ForeignKey('cities.id'))
    name = db.Column(db.String(100), nullable=False)
    address = db.Column(db.String(100), nullable=False)
    image_filename = db.Column(db.String(100), nullable=True)
    spots = db.relationship('ParkingSpot', backref='parking_lot', lazy=True, cascade="all, delete-orphan")


class ParkingSpot(db.Model, UserMixin):
    __tablename__ = 'parking_spots'
    id = db.Column(db.Integer, primary_key=True)
    parking_lot_id = db.Column(db.Integer, db.ForeignKey('parking_lots.id'), nullable=False)
    spot_number = db.Column(db.String(20), nullable=False)
    svg_coords = db.Column(db.String(100), nullable=False)
    bookings = db.relationship('Booking', back_populates='parking_spot', lazy=True)


from accounts.views import accounts_bp, passwordHasher
from dashboard.views import dashboard_bp
from booking.views import booking_bp

app.register_blueprint(accounts_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(booking_bp)
